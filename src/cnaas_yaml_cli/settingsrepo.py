from typing import List
import datetime
import git.remote
from rich.console import Console

console = Console()

class Settingsrepo():
    def __init__(self, path):
        self.path = path
        self.repo: git.Repo = git.Repo(self.path)

    def pull(self):
        origin = self.repo.remotes.origin
        origin_url = next(origin.urls)
        with console.status(f"Pulling from origin {origin_url}"):
            pullinfo: List[git.remote.FetchInfo] = origin.pull()
        last_commit = self.repo.head.commit
        last_commit_date = datetime.datetime.isoformat(last_commit.committed_datetime)
        console.log(f"Last commit: {last_commit.name_rev} at {last_commit_date}", style="green")
