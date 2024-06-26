from datetime import datetime
import os.path
from functools import cached_property
from typing import Dict, List, Optional, Sequence

import click
from tabulate import tabulate
from nex_bitrise_index import Client as BitriseIndexClient
from nex_bitrise_index.interface import AppEntry, BuildEntry
import requests
from tqdm import tqdm

from .bitrise import BitriseClient
from .git import GitInfo


class NBPCommandContext:
    def __init__(self):
        self.git_info = GitInfo.create()
        self.bitrise_index_client = BitriseIndexClient()
        self._app_name: Optional[str] = None
        self._branch: Optional[str] = None
        self._workflows: List[str] = []

    def set_app_name(self, app_name: Optional[str]) -> None:
        self._app_name = app_name

    @classmethod
    def _find_app_entry_by_git(
        cls, apps: List[AppEntry], git_info: GitInfo
    ) -> Optional[AppEntry]:
        remote_url = git_info.remote_url
        for app in apps:
            if app.repo_url == remote_url:
                return app
        return None

    @classmethod
    def _find_app_entry_by_app_name(
        cls, apps: List[AppEntry], app_name: str
    ) -> Optional[AppEntry]:
        app_name = app_name.lower()
        for app in apps:
            if app.app_code.lower() == app_name:
                return app

        # We pick the app name that is most suitable.
        for app in apps:
            # Try to find a match.
            if app.title.lower().find(app_name) != -1:
                return app
        # No match, try removing spaces.
        app_name = app_name.replace(" ", "")
        for app in apps:
            if app.title.lower().replace(" ", "").find(app_name) != -1:
                return app
        # Still no match. Try to find one that has the same sequence.
        for app in apps:
            title = app.title.lower().replace(" ", "")
            pos = -1
            for ch in app_name:
                pos = title.find(ch, pos + 1)
                if pos == -1:
                    break
            else:
                return app

        return None

    @classmethod
    def _find_app_entry(
        cls, apps: List[AppEntry], git_info: Optional[GitInfo], app_name: Optional[str]
    ) -> Optional[AppEntry]:
        if app_name is not None:
            app_entry = cls._find_app_entry_by_app_name(apps, app_name)
            if app_entry is None:
                raise click.UsageError(
                    f"Could not find app on bitrise matching {app_name}"
                )
        else:
            if git_info is None:
                raise click.UsageError(
                    "Please run inside a git repository to use auto app discovery."
                )
            app_entry = cls._find_app_entry_by_git(apps, git_info)
            if app_entry is None:
                raise click.UsageError(
                    f"Could not find app on bitrise matching git url {git_info.remote_url}"
                )
        click.echo(f"Selected App: {app_entry.app_code}")
        return app_entry

    @cached_property
    def app_entry(self) -> AppEntry:
        all_apps = self.bitrise_index_client.fetch_all_apps()
        return self._find_app_entry(all_apps, self.git_info, self._app_name)

    def initialize_branch(
        self, branch: Optional[str], use_git_branch: bool = True
    ) -> None:
        if branch:
            self._branch = branch
        elif use_git_branch:
            if self.git_info is None:
                raise click.UsageError(
                    "Auto git branch is only valid inside a git repo."
                )
            self._branch = self.git_info.remote_name
        else:
            self._branch = None

    @cached_property
    def branch(self) -> Optional[str]:
        return self._branch

    @classmethod
    def _compute_suffixes(
        cls, staging: Optional[bool], production: Optional[bool]
    ) -> List[str]:
        # prod\stag  None  True  False
        #   None      s      s
        #   True      p     sp     p
        #   False     s      s
        if staging is None and not production:
            staging = True
        suffixes = []
        if staging:
            suffixes.append("stag")
        if production:
            suffixes.append("prod")
        if not suffixes:
            raise click.UsageError("No staging nor production, bailing out.")
        return suffixes

    _WORKFLOW_MAP = {
        "olympia#-#stag": "build_olympia_apk_staging",
        "olympia#-#prod": "build_olympia_apk_production",
        "sk#-#stag": "build_sk_apk_staging",
        "sk#-#prod": "build_sk_apk_production",
        "sky#-#stag": "build_android_apk_sky_beta_staging",
        "sky#-#prod": "build_android_apk_sky_beta",
        "retail#-#stag": "build_olympia_retail_demo_stag",
        "retail#-#prod": "build_olympia_retail_demo_prod",
    }

    def initialize_workflows(
        self,
        targets: Sequence[str],
        staging: Optional[bool],
        production: Optional[bool],
    ) -> None:
        suffixes = self._compute_suffixes(staging, production)
        workflows = self._workflows
        for target in targets:
            for suffix in suffixes:
                key = f"{target}#-#{suffix}"
                if key not in self._WORKFLOW_MAP:
                    click.echo(
                        f"Cannot identify workflow id for {target} {suffix}", err=True
                    )
                    continue
                workflows.append(self._WORKFLOW_MAP[key])

    @cached_property
    def workflows(self) -> List[str]:
        return self._workflows


@click.group()
@click.option(
    "-a", "--app-name", "app_name", help="App name", type=click.STRING, default=None
)
@click.pass_context
def nbp(ctx: click.Context, app_name: Optional[str]) -> None:
    """Provide utilities of interacting with NBP projects."""
    ctx.ensure_object(NBPCommandContext)
    ctx.obj.set_app_name(app_name)


_staging_option = click.option(
    "-s",
    "--stag/--no-stag",
    "--staging/--no-staging",
    "staging",
    is_flag=True,
    default=None,
)
_production_option = click.option(
    "-p",
    "--prod/--no-prod",
    "--production/--no-production",
    "production",
    is_flag=True,
    default=None,
)
_targets_option = click.option(
    "-t",
    "--target",
    "targets",
    type=click.Choice(("olympia", "sk", "sky", "retail"), case_sensitive=False),
    multiple=True,
    default=("olympia",),
)


@nbp.command()
@click.option(
    "-b", "--branch", "branch", help="Git Branch", type=click.STRING, default=None
)
@_staging_option
@_production_option
@_targets_option
@click.option("-c", "--clean/--no-clean", "clean", is_flag=True, default=False)
@click.pass_context
def trigger(
    ctx: click.Context,
    branch: Optional[str],
    staging: Optional[bool],
    production: Optional[bool],
    targets: Sequence[str],
    clean: bool,
) -> None:
    """Trigger a build through bitrise."""
    context: NBPCommandContext = ctx.obj
    app_entry = context.app_entry
    context.initialize_branch(branch)
    context.initialize_workflows(targets, staging, production)

    bitrise_client = BitriseClient()
    for workflow_id in context.workflows:
        click.echo(f"Triggering {workflow_id} ... ", nl=False)
        (status_code, reason, response) = bitrise_client.build(
            app_entry.slug, workflow_id, context.branch, clean
        )
        if status_code != 201:
            click.echo("FAILED")
            click.echo(f"REASON: {reason}", err=True)
            click.echo(f"RESPONSE: {response}", err=True)
        else:
            click.echo(f"SUCCESS: {response['build_number']}")
            click.echo(f"    {response['build_url']}")


@nbp.command("list")
@click.pass_context
def list_projects(ctx: click.Context) -> None:
    """List configured NBP projects."""
    context: NBPCommandContext = ctx.obj
    bitrise_index_client = context.bitrise_index_client
    all_apps = bitrise_index_client.fetch_all_apps()
    table = sorted([app.title, app.repo_url] for app in all_apps)
    headers = ["TITLE", "REPO-URL"]
    click.echo(tabulate(table, headers, tablefmt="simple"))


@nbp.group("builds")
@click.option(
    "-b",
    "--branch",
    "branch",
    type=click.STRING,
    help="Specify git branch to filter with.",
    default=None,
)
@click.option(
    "-g",
    "--git-branch",
    "use_git_branch",
    is_flag=True,
    help="Use branch from current git repo.",
    default=False,
)
@_staging_option
@_production_option
@_targets_option
@click.pass_context
def builds(
    ctx: click.Context,
    branch: Optional[str],
    use_git_branch: bool,
    staging: Optional[bool],
    production: Optional[bool],
    targets: Sequence[str],
) -> None:
    """Handle build."""
    context: NBPCommandContext = ctx.obj
    context.initialize_branch(branch, use_git_branch)
    context.initialize_workflows(targets, staging, production)


@builds.command("list")
@click.option(
    "-l",
    "--limit",
    type=click.IntRange(min=0, min_open=True),
    help="Limits of entries per workflow.",
    default=3,
)
@click.pass_context
def builds_list(ctx: click.Context, limit: int) -> None:
    """Lists recent completed builds for the given app / branch."""
    context: NBPCommandContext = ctx.obj

    app_entry = context.app_entry
    bitrise_index_client = context.bitrise_index_client
    git_branch = context.branch

    headers = ("Build#", "Branch", "Time", "Post Build URL", "APK Download URL")
    for workflow in context.workflows:
        click.echo(f"Workflow {workflow}:")
        click.echo(
            tabulate(
                (
                    (
                        build_entry.build_num,
                        build_entry.branch,
                        datetime.fromtimestamp(build_entry.timestamp),
                        build_entry.post_build_url,
                        build_entry.apk_download_url,
                    )
                    for build_entry in bitrise_index_client.list_latest_builds(
                        app_entry.app_code, git_branch, workflow, limit
                    )
                ),
                headers=headers,
            )
        )


@builds.command("details")
@click.argument("build_nums", type=int, nargs=-1)
@click.pass_context
def build_details(ctx: click.Context, build_nums: Sequence[int]) -> None:
    """Show details of a build."""
    context: NBPCommandContext = ctx.obj
    app_entry = context.app_entry
    bitrise_index_client = context.bitrise_index_client
    entries = bitrise_index_client.fetch_builds(app_entry.app_code, build_nums)
    for entry in entries:
        print("==================================")
        print(f"BuildNum:  {entry.build_num}")
        print(f"Version:   {entry.app_build_num}")
        print(f"Time:      {datetime.fromtimestamp(entry.timestamp)}")
        print(f"Workflow:  {entry.workflow}")
        print(f"Branch:    {entry.branch}")
        print(f"PostBuild: {entry.post_build_url}")
        print(f"APK:       {entry.apk_download_url}")
        print(f"Tags:      {entry.tags}")
        if entry.memo:
            separator = "\n           "
            lines = entry.memo.split("\n")
            print(f"Memo:      {separator.join(lines)}")


@builds.command()
@click.argument("build_nums", type=int, nargs=-1)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, exists=True),
    help="Output directory for apks.",
    default=os.path.expanduser("~/Downloads/"),
)
@click.pass_context
def builds_apk(ctx: click.Context, build_nums: Sequence[int], output: str) -> None:
    """Download apk for the give app / branch."""
    context: NBPCommandContext = ctx.obj
    app_entry = context.app_entry
    bitrise_index_client = context.bitrise_index_client
    build_entries: List[BuildEntry] = []
    if len(build_nums) == 0:
        build_num_set = set()
        # Figure out what the latest builds are.
        git_branch = context.branch
        workflows = context.workflows
        for workflow in workflows:
            for entry in bitrise_index_client.list_latest_builds(
                app_entry.app_code, branch=git_branch, workflow=workflow, limit=1
            ):
                if entry.build_num not in build_num_set:
                    build_entries.append(entry)
                    build_num_set.add(entry.build_num)

    else:
        build_entries.extend(
            bitrise_index_client.fetch_builds(app_entry.app_code, list(set(build_nums)))
        )

    for build_entry in build_entries:
        # We are going to download one artifact at a time.
        artifact_entry = bitrise_index_client.fetch_apk(
            app_entry.app_code, build_num=build_entry.build_num
        )
        output_file = os.path.join(output, artifact_entry.name)
        click.echo(f"Downloading to {output_file}")
        click.echo(f"Download URL: {artifact_entry.download_url}")
        with requests.get(artifact_entry.download_url, stream=True) as response:
            response.raise_for_status()
            with open(output_file, "wb") as file:
                with tqdm(
                    total=artifact_entry.byte_size, unit="b", unit_scale=True
                ) as pbar:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            file.write(chunk)
                            pbar.update(len(chunk))


@builds.command("tag")
@click.option(
    "-b",
    "--build",
    type=click.INT,
    help="Build numbers to update",
    multiple=True,
)
@click.option(
    "-r", "--remove", is_flag=True, help="Remove the tags instead of adding them."
)
@click.option(
    "-t", "--tag", type=click.STRING, multiple=True, help="Tags to add/remove."
)
@click.pass_context
def builds_tag(
    ctx: click.Context, build: Sequence[int], remove: bool, tag: Sequence[str]
) -> None:
    """Tag specific builds"""
    context: NBPCommandContext = ctx.obj
    app_entry = context.app_entry
    bitrise_index_client = context.bitrise_index_client
    bitrise_index_client.update_tag(app_entry.app_code, build, remove, tag)


@builds.command("memo")
@click.option(
    "-b",
    "--build",
    type=click.INT,
    help="Build numbers to update",
    multiple=True,
)
@click.option("-e", "--edit", is_flag=True, help="Edit the memo instead of appending.")
@click.pass_context
def builds_memo(ctx: click.Context, build: Sequence[int], edit: bool) -> None:
    """Add memo to builds."""
    context: NBPCommandContext = ctx.obj
    app_entry = context.app_entry
    bitrise_index_client = context.bitrise_index_client
    template = ""
    if edit:
        builds = bitrise_index_client.fetch_builds(app_entry.app_code, build)
        # Use the first build memo as the template.
        if builds:
            template = builds[0].memo
    memo = click.edit(template)
    if not memo:
        click.echo("No memo specified", err=True)
    else:
        bitrise_index_client.add_memo(app_entry.app_code, build, edit, memo)
