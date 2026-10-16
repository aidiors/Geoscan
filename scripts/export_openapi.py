"""Export the HTTP contract without a frontend build or a running server."""

import argparse
import json
from pathlib import Path

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    application = create_app(create_default_dependencies(), serve_frontend=False)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(application.openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Exported HTTP contract to {options.output}")
