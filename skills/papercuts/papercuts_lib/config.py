import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

from .model import PapercutsError, text
from .storage import check_key


def home():
    return Path(os.environ.get("PAPERCUTS_HOME", str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "papercuts"))).expanduser().resolve()


def config_path(path=None):
    return Path(path or os.environ.get("PAPERCUTS_CONFIG", str(home() / "config.json"))).expanduser().resolve()


def normalise_storage(value):
    value = text(value, "storage")
    if "://" not in value:
        return Path(value).expanduser().resolve().as_uri()
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise PapercutsError("Storage URI cannot contain credentials, query parameters or fragments.")
    if parsed.scheme == "file":
        if parsed.netloc or not parsed.path.startswith("/"):
            raise PapercutsError("Use an absolute local file:///path URI.")
        return Path(unquote(parsed.path)).resolve().as_uri()
    if parsed.scheme not in ("s3", "r2", "gs") or not parsed.netloc:
        raise PapercutsError("Use a local path, file:///, s3://bucket/prefix, r2://bucket/prefix, or gs://bucket/prefix.")
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", parsed.netloc):
        raise PapercutsError("Invalid bucket name.")
    prefix = parsed.path.strip("/")
    if prefix:
        check_key(prefix)
    return f"{parsed.scheme}://{parsed.netloc}" + (f"/{prefix}" if prefix else "")


def validate_config(config):
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise PapercutsError("Unsupported configuration schema.")
    allowed = {"schema_version", "storage", "namespace", "author", "source", "endpoint", "region", "profile", "adapter", "options"}
    if set(config) - allowed:
        raise PapercutsError("Unknown configuration fields; credentials do not belong in config.json.")
    namespace = config.get("namespace", "default")
    if not isinstance(namespace, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", namespace):
        raise PapercutsError("Namespace must be 1–64 letters, digits, underscores or hyphens, starting with a letter/digit.")
    config["namespace"] = namespace
    for field in ("author", "source", "region", "profile"):
        if field in config:
            config[field] = text(config[field], field)
    if config.get("adapter"):
        if not re.fullmatch(r"[a-zA-Z_][\w.]*:[a-zA-Z_]\w*", config["adapter"]):
            raise PapercutsError("Adapter must be a trusted module:factory.")
        if not isinstance(config.get("options", {}), dict):
            raise PapercutsError("Adapter options must be an object.")
        return config
    config["storage"] = normalise_storage(config.get("storage"))
    endpoint = config.get("endpoint")
    if endpoint:
        url = urlsplit(endpoint)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/")):
            raise PapercutsError("Endpoint must be an HTTPS origin without credentials, path, query or fragment.")
        if not config["storage"].startswith(("s3://", "r2://")):
            raise PapercutsError("Custom endpoints are supported only for S3/R2.")
    if config["storage"].startswith("r2://") and not endpoint:
        raise PapercutsError("R2 requires --endpoint https://ACCOUNT_ID.r2.cloudflarestorage.com.")
    return config


def load(path=None):
    target = config_path(path)
    try:
        config = json.loads(target.read_text())
    except FileNotFoundError:
        if not os.environ.get("PAPERCUTS_STORAGE"):
            raise PapercutsError("Not configured. Run papercuts setup in your terminal, or provide PAPERCUTS_STORAGE.") from None
        config = {"schema_version": 1}
    except (ValueError, UnicodeError) as exc:
        raise PapercutsError("Invalid configuration JSON.") from exc
    if not isinstance(config, dict):
        raise PapercutsError("Configuration must be a JSON object.")
    config = dict(config)
    if os.environ.get("PAPERCUTS_STORAGE"):
        config.pop("adapter", None)
        config.pop("options", None)
        config["storage"] = os.environ["PAPERCUTS_STORAGE"]
    for field in ("namespace", "author", "source", "endpoint", "region", "profile"):
        if os.environ.get("PAPERCUTS_" + field.upper()):
            config[field] = os.environ["PAPERCUTS_" + field.upper()]
    return validate_config(config)


def save(config, path=None, replace=False):
    target = config_path(path)
    validate_config(config)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".config-", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(config, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise PapercutsError("Configuration exists. Use setup --replace only to intentionally change it.") from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target
