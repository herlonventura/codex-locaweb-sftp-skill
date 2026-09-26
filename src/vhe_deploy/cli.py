"""Standalone CLI. Operational results are JSON; raw exceptions are never printed."""

import base64
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import socket

import click
import paramiko
from pydantic import SecretStr
import yaml

from .config import ConfigError, Settings, Site, load_settings, load_sites
from .config.schema import AgeSettings, native_path
from .config.migration import migrate_legacy
from .connection import open_site
from .core.deploy import OperationError
from .core.guards import normalize_domain, require_registered_domain
from .core.local import private_directory
from .core.tokens import TokenError
from .operations import Runtime
from .credentials import CredentialError, CredentialKey
from .credentials.factory import selected_store
from .local_files import checked_path, write_private
from .logs import response


def emit(result):
    click.echo(json.dumps(result, ensure_ascii=True))
    code = {"success": 0, "error": 1, "conflict": 3, "partial": 4}[result["status"]]
    if code:
        click.get_current_context().exit(code)


class SafeGroup(click.Group):
    def main(self, *args, **kwargs):
        standalone = kwargs.pop("standalone_mode", True)
        try:
            result = super().main(*args, standalone_mode=False, **kwargs)
            code = result if isinstance(result, int) else 0
        except click.ClickException:
            click.echo(json.dumps(response("error", messages=["Argumentos inválidos. Consulte --help."])))
            code = 2
        except (OperationError, ConfigError, CredentialError, TokenError) as exc:
            click.echo(json.dumps(response("error", messages=[str(exc)]), ensure_ascii=True))
            code = 1
        except (Exception, KeyboardInterrupt):
            click.echo(json.dumps(response("error", messages=["Operação não concluída. Revise configuração, permissões, identidade e conexão."]), ensure_ascii=True))
            code = 1
        if standalone:
            raise SystemExit(code)
        return code


_home = Path.home() / ".vhe-deploy"


@click.group(cls=SafeGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--sites", type=click.Path(path_type=Path), default=_home / "sites.yaml", show_default=True)
@click.option("--settings", type=click.Path(path_type=Path), default=_home / "settings.yaml", show_default=True)
@click.option("--state-dir", type=click.Path(path_type=Path), default=_home / "state", show_default=True)
@click.pass_context
def cli(ctx, sites, settings, state_dir):
    """VHE Deploy: gerencie sites por SFTP/FTPS; sem exclusões remotas."""
    ctx.obj = Runtime(sites, settings, state_dir)


@cli.command("list")
@click.pass_obj
def list_sites(runtime):
    """Liste cadastros sem conectar ou ler senhas."""
    sites = load_sites(runtime.sites_file)
    emit(response("success", {"sites": [{"domain": name, "protocol": site.protocol,
         "credential_store": site.credential_store, "publish_enabled": site.publish_enabled}
         for name, site in sorted(sites.items())]}))


@cli.command("info")
@click.argument("domain")
@click.pass_obj
def info(runtime, domain):
    """Mostre configuração sem revelar credenciais."""
    sites = load_sites(runtime.sites_file)
    domain = require_registered_domain(domain, sites)
    emit(response("success", {"domain": domain, "site": sites[domain].model_dump(mode="json")}))


@cli.command("register")
@click.argument("domain")
@click.option("--entry-file", required=True, type=click.Path(path_type=Path))
@click.pass_obj
def register(runtime, domain, entry_file):
    """Cadastre um domínio novo a partir de YAML/JSON de um único site."""
    domain = normalize_domain(domain)
    entry = load_sites(entry_file)
    if set(entry) != {domain}:
        raise OperationError("O arquivo de entrada deve conter somente o domínio informado.")
    with runtime.edit_registry() as sites:
        if domain in sites:
            raise OperationError("Domínio já cadastrado; nenhuma substituição realizada.")
        sites[domain] = Site.model_validate(entry[domain].model_dump() | {"publish_enabled": False})
    emit(response("success", {"domain": domain, "publish_enabled": False}))


@cli.command("credential")
@click.argument("domain")
@click.pass_obj
def credential(runtime, domain):
    """Cadastre uma senha por prompt oculto; env apenas informa o nome da variável."""
    _, domain, site, settings = runtime.site(domain)
    site.require_connection_ready()
    key = CredentialKey.for_site(domain, site)
    store = selected_store(site, settings)
    if store.kind == "env":
        emit(response("success", {"environment_variable": key.env_name}, ["Injete a senha pelo cofre do ambiente; não a passe por argumento."]))
        return
    value = click.prompt("Senha", hide_input=True, confirmation_prompt=True, err=True)
    store.set(key, SecretStr(value))
    emit(response("success", {"domain": domain}, ["Credencial cadastrada; valor omitido."]))


@cli.command("setup")
@click.pass_obj
def setup(runtime):
    """Cadastre um site do zero por perguntas; senha somente em prompt local oculto."""
    domain = normalize_domain(click.prompt("Domínio completo", err=True))
    if runtime.sites_file.exists() and domain in load_sites(runtime.sites_file):
        raise OperationError("Domínio já cadastrado; use info para consultar ou credential para cadastrar a senha.")
    protocol = click.prompt("Protocolo", type=click.Choice(["sftp", "ftps"]), default="sftp", err=True)
    host = click.prompt("Servidor", default=domain, err=True)
    port = click.prompt("Porta", type=click.IntRange(1, 65535), default=22 if protocol == "sftp" else 21, err=True)
    user = click.prompt("Usuário", err=True)
    local_root = click.prompt("Pasta local dos arquivos do site (caminho absoluto)", err=True)
    remote_root = click.prompt("Pasta no servidor", default="/public_html", err=True)
    provider = click.prompt("Cofre da senha", type=click.Choice(["keyring", "age", "env"]), default="keyring", err=True)
    pin = None
    if protocol == "sftp":
        click.echo("Confira a chave SSH com o provedor por um canal independente. Sem essa conferência, deixe pendente.", err=True)
        pin = click.prompt("Fingerprint SHA256 confirmada (Enter para pendente)", default="", show_default=False, err=True) or None
    site = Site(protocol=protocol, host=host, port=port, user=user, local_root=local_root, remote_root=remote_root,
                ssh_fingerprint=pin, credential_store=provider)  # publication remains disabled
    settings_path = checked_path(runtime.settings_file)
    settings = load_settings(settings_path) if settings_path.exists() else Settings()
    root = checked_path(native_path(local_root))
    for private_path in (settings_path, checked_path(runtime.sites_file)):
        if private_path == root or root in private_path.parents:
            raise OperationError("Os cadastros privados devem ficar fora da pasta publicada.")
    if provider == "age" and settings.age is None:
        values = {"directory": click.prompt("Pasta privada do cofre age (fora da pasta do site)", err=True),
                  "executable": click.prompt("Caminho absoluto do executável age", err=True),
                  "identity": click.prompt("Arquivo de identidade age (Enter para pendente)", default="", show_default=False, err=True) or None,
                  "recipient": click.prompt("Chave pública age1... (Enter para pendente)", default="", show_default=False, err=True) or None}
        age = AgeSettings(**values)
        settings = Settings.model_validate(settings.model_dump() | {"age": age.model_dump()})
    if provider == "age":
        for private_value in (settings.age.directory, settings.age.identity):
            if private_value is not None:
                private_path = checked_path(native_path(private_value))
                if root == private_path or root in private_path.parents:
                    raise OperationError("O cofre e a identidade age devem ficar fora da pasta publicada.")
        private_directory(native_path(settings.age.directory))
    private_directory(settings_path.parent)
    if not settings_path.exists() or (provider == "age" and load_settings(settings_path).age is None):
        write_private(settings_path, yaml.safe_dump(settings.model_dump(mode="json")).encode("utf-8"),
                      replace=settings_path.exists())
    with runtime.edit_registry() as sites:
        if domain in sites:
            raise OperationError("Outro cadastro criou este domínio durante o assistente.")
        sites[domain] = site
    data = {"domain": domain, "publish_enabled": False, "credential_status": "pending", "connection_tested": False}
    messages = ["Cadastro salvo. A conexão não foi testada e nenhum arquivo foi enviado."]
    if protocol == "sftp" and pin is None:
        data["credential_status"] = "pending_fingerprint"
        messages.append("Use scan-key, confira por canal independente, registre com set-key e depois use credential.")
    elif provider == "env":
        data.update(credential_status="external_injection_required", environment_variable=CredentialKey.for_site(domain, site).env_name)
    else:
        try:
            store = selected_store(site, settings)
            if click.confirm("Cadastrar a senha agora?", default=True, err=True):
                value = click.prompt("Senha", hide_input=True, confirmation_prompt=True, err=True)
                store.set(CredentialKey.for_site(domain, site), SecretStr(value))
                data["credential_status"] = "stored"
        except CredentialError:
            messages.append("Cofre indisponível ou credencial não gravada; cadastro preservado. Corrija o cofre e use credential.")
    emit(response("success", data, messages))


@cli.command("scan-key")
@click.argument("domain")
@click.pass_obj
def scan_key(runtime, domain):
    """Consulte a chave SSH observada SEM autenticar, confiar ou gravar a chave."""
    _, domain, site, settings = runtime.site(domain)
    if site.protocol != "sftp":
        raise OperationError("scan-key exige SFTP.")
    with socket.create_connection((site.host, site.port), timeout=settings.timeout_seconds) as channel:
        transport = paramiko.Transport(channel)
        try:
            transport.banner_timeout = settings.timeout_seconds
            transport.start_client(timeout=settings.timeout_seconds)
            key = transport.get_remote_server_key()
            fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
        finally:
            transport.close()
    emit(response("success", {"domain": domain, "fingerprint": fingerprint, "verified": False},
                  ["Confira a chave por um canal independente antes de usar set-key."]))


@cli.command("set-key")
@click.argument("domain")
@click.option("--fingerprint", required=True)
@click.option("--confirm", is_flag=True, help="Declara conferência da chave por canal independente.")
@click.pass_obj
def set_key(runtime, domain, fingerprint, confirm):
    if not confirm:
        raise OperationError("set-key exige --confirm após conferência independente da chave.")
    with runtime.edit_registry() as sites:
        domain = require_registered_domain(domain, sites)
        sites[domain] = Site.model_validate(sites[domain].model_dump() | {"ssh_fingerprint": fingerprint})
    emit(response("success", {"domain": domain}, ["Chave registrada; uma mudança de vínculo exige recadastro da credencial."]))


@cli.command("test")
@click.argument("domain")
@click.pass_obj
def test_connection(runtime, domain):
    sites, domain, _, settings = runtime.site(domain)
    with open_site(sites, domain, settings=settings) as backend:
        backend.check_connection()
    emit(response("success", {"domain": domain}, ["Conexão e raiz verificadas."]))


@cli.command("compare")
@click.argument("domain")
@click.pass_obj
def compare(runtime, domain):
    """Compare conteúdo e datas, sem alterar o servidor."""
    emit(runtime.analyze(domain))


@cli.command("preview")
@click.argument("domain")
@click.pass_obj
def preview(runtime, domain):
    """Calcule a prévia e emita token de uso único válido por cinco minutos."""
    emit(runtime.analyze(domain, issue_token=True))


@cli.command("backup")
@click.argument("domain")
@click.pass_obj
def backup(runtime, domain):
    """Baixe e verifique os arquivos remotos na pasta privada de registros."""
    emit(runtime.backup_site(domain))


@cli.command("deploy")
@click.argument("domain")
@click.option("--preview-hash", required=True, help="SHA-256 retornado pela prévia revisada.")
@click.option("--preview-token", required=True, help="Token emitido por preview; válido por cinco minutos, uso único.")
@click.option("--confirm", is_flag=True)
@click.pass_obj
def deploy_command(runtime, domain, preview_hash, preview_token, confirm):
    """Publique com backup/verificação; retenha três envios, sem exclusões remotas."""
    emit(runtime.deploy_site(domain, preview_hash, preview_token, confirm))


@cli.command("migrate")
@click.option("--from", "source", required=True, type=click.Path(path_type=Path))
@click.option("--to", "destination", required=True, type=click.Path(path_type=Path))
@click.option("--local-root", "mappings", multiple=True, help="Mapeamento explícito dominio=caminho; pode repetir.")
def migrate(source, destination, mappings):
    roots = {}
    for item in mappings:
        domain, separator, path = item.partition("=")
        domain = normalize_domain(domain)
        if not separator or not path or domain in roots:
            raise OperationError("Mapeamento local inválido ou repetido.")
        roots[domain] = path
    result = migrate_legacy(source, destination, local_roots=roots)
    emit(response("success", asdict(result), ["Cadastros migrados; publicação desativada e senhas pendentes de recadastro."]))


@cli.command("help")
@click.pass_context
def help_command(ctx):
    click.echo(ctx.parent.get_help())


for name, command in {"listar": list_sites, "comparar": compare, "previa": preview,
                       "enviar": deploy_command, "testar": test_connection, "migrar": migrate,
                       "configurar": setup}.items():
    cli.add_command(command, name)
