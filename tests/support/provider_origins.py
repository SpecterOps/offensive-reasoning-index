"""Immutable synthetic origin-admission cases; no runtime fixture behavior."""

SCOPED_HOSTS = (
    ("openrouter.ai", "openrouter", "OPENROUTER_API_KEY", "test-router"),
    ("api.openrouter.ai", "openrouter", "OPENROUTER_API_KEY", "test-router"),
    ("inference-api.nousresearch.com", "nous", "NOUS_API_KEY", "test-nous"),
    ("portal.nousresearch.com", "nous", "NOUS_API_KEY", "test-nous"),
)
SYNTHETIC_KEYS = (
    ("OPENAI_API_KEY", "test-openai"),
    ("OPENAI_COMPAT_API_KEY", "test-generic"),
    ("OPENROUTER_API_KEY", "test-router"),
    ("NOUS_API_KEY", "test-nous"),
    ("NOUS_PORTAL_API_KEY", "test-nous-alias"),
)
CLEARED_ENVIRONMENT = tuple(name for name, _ in SYNTHETIC_KEYS) + (
    "OPENAI_BASE_URL", "OPENAI_COMPAT_BASE_URL",
)
VALID_ORIGINS = tuple(
    (f"H{host_index}-V{form_index}", url, family, source, key)
    for host_index, (host, family, source, key) in enumerate(SCOPED_HOSTS)
    for form_index, url in enumerate((
        f"https://{host}/v1", f"https://{host}:443/v1", f"HTTPS://{host.upper()}/v1",
    ))
)
DENIED_ORIGINS = tuple(
    (f"H{host_index}-D{form_index}", url, family, source, key)
    for host_index, (host, family, source, key) in enumerate(SCOPED_HOSTS)
    for form_index, url in enumerate((
        f"http://{host}/v1", f"ftp://{host}/v1", f"https://{host}:8443/v1",
        f"https://{host}:bad/v1", f"https://{host}:65536/v1", f"https://user@{host}/v1",
        f"https://user:pass@{host}/v1", f"https://@{host}/v1",
    ))
)
ALIAS_ORIGINS = tuple(
    (f"H{index}-alias", f"https://{host}/v1", family, "NOUS_PORTAL_API_KEY", "test-nous-alias")
    for index, (host, family, _, _) in enumerate(SCOPED_HOSTS) if family == "nous"
)
COMPATIBILITY_ORIGINS = (
    ("http://custom.example:8080/v1", "generic", "test-generic"),
    ("https://custom.example:8443/v1", "generic", "test-generic"),
    ("http://127.0.0.1:8080/v1", "generic", "test-generic"),
    ("https://openrouter.ai.example.test/v1", "generic", "test-generic"),
    ("https://inference-api.nousresearch.com.example.test/v1", "generic", "test-generic"),
    ("https://api.openai.com/v1", "openai", "test-openai"),
    ("http://api.openai.com/v1", "openai", None),
    ("https://api.openai.com:8443/v1", "openai", None),
)
