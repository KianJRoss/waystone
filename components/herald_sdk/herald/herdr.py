"""Host-qualified Herdr inventory and explicit routing over its socket-backed CLI.

No servers are installed/restarted, and an unreachable host never becomes Local.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


class HerdrError(RuntimeError):
    pass


class Herdr:
    def __init__(self, machine: str | None = None):
        self.machine = machine

    def call(self, *args: str, timeout: float = 15) -> dict:
        if os.environ.get("HERDR_ENV") != "1":
            raise HerdrError("Herdr access requires HERDR_ENV=1")
        command = ["herdr", *(["--machine", self.machine] if self.machine else []), *args]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HerdrError("Herdr command failed; inspect state before retrying a mutation") from exc
        if result.returncode:
            raise HerdrError(f"Herdr command failed (exit {result.returncode}); inspect host state")
        if not result.stdout.strip():
            return {}  # pane run/send commands acknowledge by exit status only.
        try:
            body = json.loads(result.stdout)
        except ValueError as exc:
            raise HerdrError("Invalid Herdr response") from exc
        if isinstance(body, dict) and body.get("error"):
            raise HerdrError("Herdr rejected the request")
        return body.get("result", body) if isinstance(body, dict) else body


def local_machine_id(root: Path | None = None) -> str:
    """A persistent installation identity; labels and SSH aliases may change."""
    if os.environ.get("HERALD_MACHINE_ID"):
        return str(uuid.UUID(os.environ["HERALD_MACHINE_ID"]))
    root = root or Path(os.environ.get("HERALD_DATA_DIR", str(Path.home() / ".herald")))
    root.mkdir(parents=True, exist_ok=True)
    path = root / "herdr-machine-id"
    temporary = root / (".machine-id-" + uuid.uuid4().hex)
    temporary.write_text(str(uuid.uuid4()))
    try:
        try:
            os.link(temporary, path)  # Publish complete bytes once, including concurrent workers.
        except FileExistsError:
            pass
    finally:
        temporary.unlink()
    return str(uuid.UUID(path.read_text().strip()))


@dataclass(frozen=True)
class Host:
    machine_id: str
    machine_label: str
    session_id: str = "default"
    selector: str | None = None


class FleetDirectory:
    """An explicit host map. Remote selectors are saved Herdr profile IDs/labels.

    machine_id must be copied from the remote installation, not inferred from
    this client's SSH profile ID. Profiles are observer-local connection handles.
    """
    def __init__(self, hosts: list[Host], client=Herdr):
        keys = [(h.machine_id, h.session_id) for h in hosts]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate machine/session identity")
        if any(not h.machine_id or not h.machine_label or not h.session_id for h in hosts):
            raise ValueError("Hosts need machine identity, label and session")
        self.hosts, self.client = hosts, client

    def _agents(self, host: Host) -> list[dict]:
        if host.selector:
            profiles = self.client().call("machine", "list", "--json")
            matches = [p for p in profiles if p.get("enabled") and host.selector in (p["id"], p["label"])]
            if len(matches) != 1 or matches[0].get("session", "default") != host.session_id:
                raise HerdrError("Saved machine profile is missing, ambiguous, or has a different session")
        return self.client(host.selector).call("agent", "list")["agents"]

    @staticmethod
    def qualify(host: Host, agent: dict) -> dict:
        session = agent.get("agent_session") or {}
        occupant = session.get("value") or (agent.get("tokens") or {}).get("run_id") or agent["terminal_id"]
        identity = json.dumps([host.machine_id, host.session_id, agent["terminal_id"],
                               agent.get("agent"), occupant])
        tokens = agent.get("tokens") or {}
        return {**agent, **asdict(host), "pane_host": host.machine_id, "agent_instance_id": str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
                "identity_confidence": "session" if session.get("value") else ("managed" if tokens.get("run_id") else "terminal"),
                "execution_host": tokens.get("execution_host") or (host.machine_id if agent.get("agent") != "Herald" else None),
                "inference_host": tokens.get("inference_host"), "model": tokens.get("model")}

    def inventory(self) -> dict:
        agents, hosts = [], []
        for host in self.hosts:
            try:
                agents.extend(self.qualify(host, a) for a in self._agents(host))
                hosts.append({**asdict(host), "reachable": True})
            except (HerdrError, KeyError) as exc:
                hosts.append({**asdict(host), "reachable": False, "error": str(exc)})
        return {"hosts": hosts, "agents": agents}

    def prompt(self, address: dict, text: str) -> dict:
        """Revalidate the occupant, submit once. Never retry uncertain delivery."""
        host = next((h for h in self.hosts if h.machine_id == address["machine_id"]
                     and h.session_id == address["session_id"]), None)
        if host is None:
            raise HerdrError("Unknown machine/session")
        agent = next((self.qualify(host, a) for a in self._agents(host)
                      if a["pane_id"] == address["pane_id"]), None)
        if not agent or agent["agent_instance_id"] != address["agent_instance_id"]:
            raise HerdrError("Agent occupant changed; refresh inventory")
        if (agent.get("tokens") or {}).get("execution_mode") == "observer":
            raise HerdrError("Read-only worker view: the director owns execution")
        if (agent.get("tokens") or {}).get("execution_mode") == "one-shot":
            raise HerdrError("One-shot worker: dispatch a new UA attempt instead of writing to its terminal")
        if agent["identity_confidence"] == "terminal" and agent.get("revision") != address.get("revision"):
            raise HerdrError("Unverified native session changed; refresh inventory")
        return self.client(host.selector).call("agent", "prompt", agent["pane_id"], text)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", type=Path, help="JSON list of Host records; default: local installation")
    args = parser.parse_args()
    root = Path(os.environ.get("HERALD_DATA_DIR", str(Path.home() / ".herald")))
    hosts = ([Host(**h) for h in json.loads(args.hosts.read_text())] if args.hosts else
             [Host(local_machine_id(root), socket.gethostname(), os.environ.get("HERDR_SESSION_NAME", "default"))])
    print(json.dumps(FleetDirectory(hosts).inventory(), indent=2))


if __name__ == "__main__":
    main()
