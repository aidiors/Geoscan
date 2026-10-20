"""Container smoke check: save a workspace, recreate the container, read it back."""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def run(mode: str, record: Path) -> None:
    base = "http://127.0.0.1:8000/v1/projects"
    if mode == "save":
        payload = {
            "name": "Container persistence smoke",
            "document": {
                "editor_state": {"smoke_check": True},
                "source_files": [
                    {"name": "source.kml", "zone_type": "work_area", "content": "<kml/>"}
                ],
            },
        }
        request = Request(
            base, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
        record.write_text(json.dumps(result), encoding="utf-8")
    else:
        expected = json.loads(record.read_text(encoding="utf-8"))
        with urlopen(f"{base}/{expected['project_id']}", timeout=10) as response:
            actual = json.load(response)
        if actual != expected:
            raise RuntimeError("Project revision changed after container recreation")
        print("Project and original source survived container recreation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["save", "check"])
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    run(args.mode, args.record)
