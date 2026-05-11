import db
import security
import indexer
import git_client


async def handle(args: dict, session_id: int | None) -> dict:
    repo_url = args.get("repo_url", "").strip()

    if not repo_url:
        return {"error": "Se requiere 'repo_url'"}

    name, path = git_client.clone_repo(repo_url)

    project_id = db.insert_project(name, path, repo_url)
    security.add_allowed_path(path)

    files_indexed = indexer.index_project(project_id, path)

    return {
        "project": name,
        "path": path,
        "files_indexed": files_indexed,
    }
