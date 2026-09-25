"""Stdlib-only HTTP client for the standalone worker agent (etl/worker_agent.py).

Kept separate from etl/web.py's own HTTP plumbing: this is the server acting
as a *client* of another process, not serving requests.
"""
import json
import urllib.error
import urllib.request

from .spec import ConfigError


def _call(method, url, token, path, body=None, timeout=10):
    request = urllib.request.Request(
        url.rstrip("/") + path, method=method,
        headers={"X-Worker-Token": token, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except (ValueError, TypeError):
            return error.code, {"error": "Worker returned an unreadable error response"}
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
        raise ConfigError(f"Worker unreachable: {error}") from None
    except ValueError:
        raise ConfigError("Worker returned an invalid response") from None


def dispatch(url, token, run_id, spec, timeout=10):
    """POST /run. Raises ConfigError if the worker refuses or is unreachable."""
    status, body = _call("POST", url, token, "/run", {"run_id": run_id, "spec": spec}, timeout=timeout)
    if status not in (200, 202):
        raise ConfigError(body.get("error") or f"Worker refused the job (HTTP {status})")
    return body


def poll(url, token, run_id, timeout=10):
    """GET /status/{run_id}. None means the worker does not know this run_id
    (not yet visible, or the worker restarted) - not necessarily a failure."""
    status, body = _call("GET", url, token, f"/status/{run_id}", timeout=timeout)
    if status == 404:
        return None
    if status != 200:
        raise ConfigError(body.get("error") or f"Worker status check failed (HTTP {status})")
    return body


def health(url, token, timeout=5):
    """Never raises: a health check failure is a status, not an error."""
    try:
        status, body = _call("GET", url, token, "/health", timeout=timeout)
    except ConfigError as error:
        return {"online": False, "error": str(error)}
    if status != 200:
        return {"online": False, "error": body.get("error") or f"HTTP {status}"}
    return {"online": True, **body}
