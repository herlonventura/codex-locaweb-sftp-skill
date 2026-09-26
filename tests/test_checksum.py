import pytest

from mcp_locaweb_sftp.core.checksum import normalize_sha256, sha256_bytes, sha256_chunks


@pytest.mark.parametrize(("content", "expected"), [
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
])
def test_known_sha256_vectors(content, expected):
    assert sha256_bytes(content) == expected
    assert sha256_chunks(bytes([byte]) for byte in content) == expected


def test_chunks_do_not_normalize_content():
    assert sha256_chunks([b"a", b"", b"bc"]) == sha256_bytes(b"abc")
    assert sha256_bytes(b"a\r\n") != sha256_bytes(b"a\n")


@pytest.mark.parametrize("value", ["a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n", None])
def test_missing_or_malformed_digest_is_not_trusted(value):
    with pytest.raises(ValueError):
        normalize_sha256(value)


def test_digest_case_is_not_a_content_difference():
    assert normalize_sha256("AB" * 32) == "ab" * 32


@pytest.mark.parametrize("content", ["abc", "C:/sites/index.html", None, 123])
def test_hashing_cannot_treat_text_as_a_path(content):
    with pytest.raises(TypeError):
        sha256_bytes(content)


def test_bad_stream_chunk_does_not_produce_a_partial_hash():
    with pytest.raises(TypeError):
        sha256_chunks([b"first", "not bytes", b"last"])
