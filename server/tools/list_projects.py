import db


async def handle(args: dict, session_id: int | None) -> dict:
    projects = db.get_all_projects()
    return {
        "projects": [
            {
                "name": p["name"],
                "last_indexed": p["last_indexed_at"].isoformat() if p["last_indexed_at"] else None,
                "repo_url": p["repo_url"],
            }
            for p in projects
        ]
    }
