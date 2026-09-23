"""Local startup configuration. Never log connection strings or file contents."""
import os
import re


def load_workspace_env(root, *, environ=None, drivers=None):
    """Read literal KEY=value settings; existing process settings take precedence.

    Quotes around the complete value are stripped. No shell evaluation, variable
    interpolation or escape decoding: SQL passwords are literal data.
    """
    env = os.environ if environ is None else environ
    path = root / ".env"
    settings = {}
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"Invalid .env assignment on line {number}; use KEY=value")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"Unclosed .env quote on line {number}")
                value = value[1:-1]
            settings[key] = value
    for key, value in settings.items():
        env.setdefault(key, value)

    # Existing explicit connection strings remain authoritative. Legacy DB_*
    # fields are a convenience for this application's original local setup.
    if env.get("ETL_SQL_MAIN") or not env.get("DB_HOST"):
        return
    if not env.get("DB_NAME"):
        raise ValueError("DB_HOST requires DB_NAME in the environment or .env")
    trusted = env.get("DB_TRUSTED_CONNECTION", "no").lower()
    if trusted not in {"yes", "no"}:
        raise ValueError("DB_TRUSTED_CONNECTION must be yes or no")
    if trusted == "yes":
        # The Windows identity running this process is the credential; a SQL
        # login alongside it would be silently ignored by the driver, which
        # is worse than refusing outright.
        if env.get("DB_USER") or env.get("DB_PASS"):
            raise ValueError("DB_TRUSTED_CONNECTION=yes uses the Windows identity; remove DB_USER and DB_PASS")
    elif not env.get("DB_USER") or not env.get("DB_PASS"):
        raise ValueError("DB_HOST requires DB_USER and DB_PASS, or DB_TRUSTED_CONNECTION=yes for Windows Authentication")
    driver = env.get("DB_DRIVER")
    if not driver:
        if drivers is None:
            import pyodbc
            drivers = pyodbc.drivers()
        driver = next((name for name in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server") if name in drivers), None)
        if not driver:
            raise ValueError("Install SQL Server ODBC Driver 18 or 17, or set DB_DRIVER explicitly")
    server = env["DB_HOST"]
    port = env.get("DB_PORT", "")
    if port:
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("DB_PORT must be an integer from 1 to 65535")
        if "," not in server:
            server += "," + port
    encrypt = env.get("DB_ENCRYPT", "yes").lower()
    trust = env.get("DB_TRUST_SERVER_CERTIFICATE", "no").lower()
    if encrypt not in {"yes", "no"} or trust not in {"yes", "no"}:
        raise ValueError("DB_ENCRYPT and DB_TRUST_SERVER_CERTIFICATE must be yes or no")
    values = {"DRIVER": driver, "SERVER": server, "DATABASE": env["DB_NAME"]}
    if trusted == "yes":
        values["Trusted_Connection"] = "yes"
    else:
        values["UID"], values["PWD"] = env["DB_USER"], env["DB_PASS"]
    values["Encrypt"], values["TrustServerCertificate"], values["APP"] = encrypt, trust, "ETL Studio"
    env["ETL_SQL_MAIN"] = ";".join(key + "={" + value.replace("}", "}}") + "}" for key, value in values.items())
