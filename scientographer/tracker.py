# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Set up the local Hamilton UI so tracked runs have somewhere to go.

A fresh Hamilton UI database has no project and no users, and the Hamilton tracker
refuses to start a run until the project it reports to exists ("Project 1 does not
exist"). ``scientographer ui`` therefore calls ``provision`` right after the server
has booted: it creates (or finds) the tracking user and the project, and gives the
user write access. It is idempotent.

Creating the project through the UI's REST API is not an option in the Hamilton UI
versions this package supports (the endpoint fails while serialising its response),
so this goes through the UI's own Django models instead, in a separate process
pointed at the same database directory.

Run as ``python -m scientographer.tracker <base_dir> <username> <project_name>``;
prints the project id.
"""

import os
from pathlib import Path
import sys


def provision(base_dir: Path, username: str, project_name: str) -> int:
    """Create or find the user and the project in the Hamilton UI database at
    ``base_dir``; return the project id. Must run in its own process (Django)."""
    import hamilton_ui

    os.environ["HAMILTON_BASE_DIR"] = str(Path(base_dir).resolve())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings_mini")
    sys.path.insert(0, os.path.dirname(hamilton_ui.__file__))
    import django

    django.setup()
    from django.core.management import call_command

    Path(base_dir).mkdir(parents=True, exist_ok=True)
    call_command("migrate", verbosity=0)  # no-op when the server already created the tables
    from trackingserver_auth.models import User
    from trackingserver_projects.models import Project, ProjectUserMembership

    user, _ = User.objects.get_or_create(
        email=username, defaults={"first_name": username}
    )
    project, _ = Project.objects.get_or_create(
        name=project_name,
        defaults={"description": "Scientographer pipeline runs", "creator": user, "tags": {}},
    )
    ProjectUserMembership.objects.get_or_create(project=project, user=user, defaults={"role": "write"})
    return int(project.id)


if __name__ == "__main__":
    base_dir, username, project_name = sys.argv[1:4]
    print(provision(Path(base_dir), username, project_name))
