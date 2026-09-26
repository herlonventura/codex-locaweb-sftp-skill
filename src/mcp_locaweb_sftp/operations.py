"""Application operations shared by the local CLI and the stdio MCP server."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import Site, load_settings, load_sites
from .config.schema import native_path
from .connection import open_site
from .core.deploy import OperationError, backup_site, deploy, publication_guard, state_directory
from .core.guards import normalize_domain, require_registered_domain
from .core.local import private_directory
from .core.preview import make_preview
from .core.tokens import TokenStore
from .local_files import checked_path, write_private
from .logs import response


@dataclass(frozen=True)
class Runtime:
    sites_file: Path
    settings_file: Path
    state: Path

    def site(self, domain):
        sites = load_sites(self.sites_file)
        domain = require_registered_domain(domain, sites)
        return sites, domain, sites[domain], load_settings(self.settings_file)

    @contextmanager
    def edit_registry(self):
        path = checked_path(self.sites_file)
        if path.suffix.lower() not in (".yaml", ".yml"):
            raise OperationError("Use migrate antes de editar o cadastro JSON legado.")
        private_directory(path.parent)
        lock = path.with_name(path.name + ".lock")
        try:
            lock.mkdir(mode=0o700)
        except FileExistsError:
            raise OperationError("Cadastro em edição ou trava residual; revise antes de continuar.") from None
        try:
            sites = load_sites(path) if path.exists() else {}
            yield sites
            content = yaml.safe_dump({domain: site.model_dump(mode="json") for domain, site in sorted(sites.items())},
                                     sort_keys=False, allow_unicode=True).encode("utf-8")
            write_private(path, content, replace=True)
        finally:
            checked_path(lock).rmdir()

    def list_sites(self):
        sites = load_sites(self.sites_file)
        return response("success", {"sites": [{"domain": name, "protocol": site.protocol,
                        "credential_store": site.credential_store, "publish_enabled": site.publish_enabled}
                        for name, site in sorted(sites.items())]})

    def register_site(self, domain, config):
        domain = normalize_domain(domain)
        site = Site.model_validate(config)
        # The MCP cannot confer trust in an SSH key, select a custom TLS CA,
        # enable publishing or weaken FTPS conditions. Those are local actions.
        if site.ssh_fingerprint is not None or site.ca_file is not None or site.publish_enabled or site.ftps_write_preconditions_confirmed:
            raise OperationError("Confirme identidade e permissões de publicação localmente; cadastro MCP deve começar pendente e desativado.")
        root = checked_path(native_path(site.local_root))
        for path in (checked_path(self.sites_file), checked_path(self.settings_file)):
            if root == path or root in path.parents:
                raise OperationError("Os cadastros privados devem ficar fora da pasta publicada.")
        with self.edit_registry() as sites:
            if domain in sites:
                raise OperationError("Domínio já cadastrado; nenhuma substituição realizada.")
            sites[domain] = site
        return response("success", {"domain": domain, "publish_enabled": False, "credential_status": "pending"},
                        ["Cadastro salvo sem conexão. Confirme a identidade e cadastre a senha no terminal local."])

    def test_connection(self, domain):
        sites, domain, _, settings = self.site(domain)
        with open_site(sites, domain, settings=settings) as backend:
            backend.check_connection()
        return response("success", {"domain": domain}, ["Conexão e raiz verificadas."])

    def analyze(self, domain, *, issue_token=False):
        sites, domain, site, settings = self.site(domain)
        with open_site(sites, domain, settings=settings) as backend:
            preview = make_preview(domain, site, settings, backend)
        data = preview.summary()
        if issue_token and not preview.comparison.has_blockers:
            data.update(TokenStore(state_directory(site, self.state)).issue(preview))
        return response("conflict" if preview.comparison.has_blockers else "success", data,
                        ["Prévia calculada; nenhum arquivo enviado ou excluído."])

    def backup_site(self, domain):
        sites, domain, site, settings = self.site(domain)
        with open_site(sites, domain, settings=settings) as backend:
            return backup_site(domain, site, backend, state=self.state)

    def deploy_site(self, domain, preview_hash, preview_token, confirm=False):
        sites, domain, site, settings = self.site(domain)
        publication_guard(site, settings, confirm)
        TokenStore(state_directory(site, self.state)).validate(domain, preview_hash, preview_token)
        with open_site(sites, domain, settings=settings) as backend:
            return deploy(domain, site, settings, backend, state=self.state, preview_hash=preview_hash,
                          preview_token=preview_token, confirm=confirm)
