import os
import time
from pathlib import Path, PurePosixPath

try:
    import pygit2

    ANY_GIT_ERROR = [
        pygit2.GitError,
        pygit2.AlreadyExistsError,
        pygit2.InvalidSpecError,
    ]
except ImportError:
    git = None
    ANY_GIT_ERROR = []

import pathspec

from aider import prompts, utils

from .dump import dump  # noqa: F401

ANY_GIT_ERROR += [
    OSError,
    IndexError,
    BufferError,
    TypeError,
    ValueError,
    AttributeError,
    AssertionError,
    TimeoutError,
]
ANY_GIT_ERROR = tuple(ANY_GIT_ERROR)


class GitRepo:
    repo = None
    aider_ignore_file = None
    aider_ignore_spec = None
    aider_ignore_ts = 0
    aider_ignore_last_check = 0
    subtree_only = False
    ignore_file_cache = {}
    git_repo_error = None

    @classmethod
    def init(cls, path=None):
        """Initialize a new Git repository"""
        if path is None:
            path = "."
        path = Path(path).resolve()
        repo = pygit2.init_repository(str(path))
        return cls(io=None, fnames=[], git_dname=str(path))

    def __init__(
        self,
        io=None,
        fnames=None,
        git_dname=None,
        aider_ignore_file=None,
        models=None,
        attribute_author=True,
        attribute_committer=True,
        attribute_commit_message_author=False,
        attribute_commit_message_committer=False,
        commit_prompt=None,
        subtree_only=False,
    ):
        self.io = io
        self.models = models

        self.normalized_path = {}
        self.tree_files = {}

        self.attribute_author = attribute_author
        self.attribute_committer = attribute_committer
        self.attribute_commit_message_author = attribute_commit_message_author
        self.attribute_commit_message_committer = attribute_commit_message_committer
        self.commit_prompt = commit_prompt
        self.subtree_only = subtree_only
        self.ignore_file_cache = {}

        if git_dname:
            check_fnames = [git_dname]
        elif fnames:
            check_fnames = fnames
        else:
            check_fnames = ["."]

        repo_paths = []
        for fname in check_fnames:
            fname = Path(fname)
            fname = fname.resolve()

            if not fname.exists() and fname.parent.exists():
                fname = fname.parent

            try:
                repo_path = pygit2.discover_repository(str(fname))
                if repo_path:
                    repo_path = utils.safe_abs_path(Path(repo_path).parent)
                    repo_paths.append(repo_path)
            except pygit2.GitError:
                pass

        num_repos = len(set(repo_paths))

        if num_repos == 0:
            raise FileNotFoundError
        if num_repos > 1:
            self.io.tool_error("Files are in different git repos.")
            raise FileNotFoundError

        self.repo = pygit2.Repository(repo_paths.pop())
        self.root = utils.safe_abs_path(self.repo.workdir)

        if aider_ignore_file:
            self.aider_ignore_file = Path(aider_ignore_file)

        # Initialize Git interface
        self.git = self.Git(self.repo)
        self.index = self.repo.index

    @property
    def git(self):
        """A simple Git interface to mimic GitPython's git attribute using pygit2."""
        if not hasattr(self, '_git'):
            self._git = self.Git(self.repo)
        return self._git

    @property
    def index(self):
        """Access to the repository index"""
        return self.repo.index

    class Git:
        """A simple Git interface to mimic GitPython's git attribute using pygit2."""

        def __init__(self, repo):
            self.repo = repo

        def add(self, filename):
            try:
                rel_path = os.path.relpath(filename, self.repo.workdir)
                self.repo.index.add(rel_path)
                self.repo.index.write()
            except Exception as e:
                raise AttributeError(f"Failed to add file '{filename}': {e}")

        def commit(self, message, author=None, committer=None):
            try:
                if author is None:
                    author = f"{self.repo.config['user.name']} <{self.repo.config['user.email']}>"
                if committer is None:
                    committer = author

                author_signature = pygit2.Signature(*author.split(" <"))
                committer_signature = pygit2.Signature(*committer.split(" <"))
                tree = self.repo.index.write_tree()
                parents = [self.repo.head.target] if not self.repo.head_is_unborn else []
                self.repo.create_commit(
                    'HEAD',
                    author_signature,
                    committer_signature,
                    message,
                    tree,
                    parents
                )
            except Exception as e:
                raise AttributeError(f"Failed to commit: {e}")

        def diff(self, a="HEAD", b=None):
            try:
                if b is None:
                    b = self.repo.head.target
                diff = self.repo.diff(a, b)
                return diff.patch
            except Exception as e:
                raise AttributeError(f"Failed to generate diff: {e}")

        def ls_files(self):
            """List all tracked files in the repository"""
            try:
                return [entry.path for entry in self.repo.index]
            except Exception as e:
                raise AttributeError(f"Failed to list files: {e}")

    def commit(self, fnames=None, context=None, message=None, aider_edits=False):
        if not fnames and not self.repo.status():
            return

        diffs = self.get_diffs(fnames)
        if not diffs:
            return

        if message:
            commit_message = message
        else:
            commit_message = self.get_commit_message(diffs, context)

        if aider_edits and self.attribute_commit_message_author:
            commit_message = "aider: " + commit_message
        elif self.attribute_commit_message_committer:
            commit_message = "aider: " + commit_message

        if not commit_message:
            commit_message = "(no commit message provided)"

        full_commit_message = commit_message

        original_user_name = self.repo.config["user.name"]
        original_committer_name_env = os.environ.get("GIT_COMMITTER_NAME")
        committer_name = f"{original_user_name} (aider)"

        if self.attribute_committer:
            os.environ["GIT_COMMITTER_NAME"] = committer_name

        if aider_edits and self.attribute_author:
            original_author_name_env = os.environ.get("GIT_AUTHOR_NAME")
            os.environ["GIT_AUTHOR_NAME"] = committer_name

        try:
            # Stage files
            if fnames:
                for fname in fnames:
                    try:
                        rel_path = str(Path(fname).relative_to(self.root))
                        self.repo.index.add(rel_path)
                    except (ValueError, pygit2.GitError) as err:
                        self.io.tool_error(f"Unable to add {fname}: {err}")
            else:
                self.repo.index.add_all()

            # Create commit using the Git interface
            git_interface = self.git
            git_interface.commit(full_commit_message)

            commit_hash = git_interface.repo.revparse_single('HEAD').hex[:7]
            self.io.tool_output(f"Commit {commit_hash} {commit_message}", bold=True)
            return commit_hash, commit_message
        except pygit2.GitError as err:
            self.io.tool_error(f"Unable to commit: {err}")
        finally:
            # Restore the env
            if self.attribute_committer:
                if original_committer_name_env is not None:
                    os.environ["GIT_COMMITTER_NAME"] = original_committer_name_env
                else:
                    del os.environ["GIT_COMMITTER_NAME"]

            if aider_edits and self.attribute_author:
                if original_author_name_env is not None:
                    os.environ["GIT_AUTHOR_NAME"] = original_author_name_env
                else:
                    del os.environ["GIT_AUTHOR_NAME"]

    def get_rel_repo_dir(self):
        try:
            return os.path.relpath(self.repo.git_dir, os.getcwd())
        except (ValueError, OSError):
            return self.repo.git_dir

    def get_commit_message(self, diffs, context):
        diffs = "# Diffs:\n" + diffs

        content = ""
        if context:
            content += context + "\n"
        content += diffs

        system_content = self.commit_prompt or prompts.commit_system
        messages = [
            dict(role="system", content=system_content),
            dict(role="user", content=content),
        ]

        commit_message = None
        for model in self.models:
            num_tokens = model.token_count(messages)
            max_tokens = model.info.get("max_input_tokens") or 0
            if max_tokens and num_tokens > max_tokens:
                continue
            commit_message = model.simple_send_with_retries(messages)
            if commit_message:
                break

        if not commit_message:
            self.io.tool_error("Failed to generate commit message!")
            return

        commit_message = commit_message.strip()
        if commit_message and commit_message[0] == '"' and commit_message[-1] == '"':
            commit_message = commit_message[1:-1].strip()

        return commit_message

    def get_diffs(self, fnames=None):
        # We always want diffs of index and working dir

        current_branch_has_commits = False
        try:
            active_branch = self.repo.active_branch
            try:
                commits = self.repo.iter_commits(active_branch)
                current_branch_has_commits = any(commits)
            except ANY_GIT_ERROR:
                pass
        except (TypeError,) + ANY_GIT_ERROR:
            pass

        if not fnames:
            fnames = []

        diffs = ""
        for fname in fnames:
            if not self.path_in_repo(fname):
                diffs += f"Added {fname}\n"

        try:
            if current_branch_has_commits:
                args = ["HEAD", "--"] + list(fnames)
                diffs += self.repo.diff(*args).patch
                return diffs

            wd_args = ["--"] + list(fnames)
            index_diffs = self.repo.diff("HEAD", None, paths=fnames).patch
            working_diffs = self.repo.diff(None, paths=fnames).patch
            diffs += index_diffs
            diffs += working_diffs

            return diffs
        except ANY_GIT_ERROR as err:
            self.io.tool_error(f"Unable to diff: {err}")

    def diff_commits(self, pretty, from_commit, to_commit):
        args = []
        if pretty:
            args += ["--color"]
        else:
            args += ["--color=never"]

        args += [from_commit, to_commit]
        diffs = self.repo.git.diff(*args)

        return diffs

    def get_tracked_files(self):
        if not self.repo:
            return []

        try:
            commit = self.repo.head.commit
        except ValueError:
            commit = None
        except ANY_GIT_ERROR as err:
            self.git_repo_error = err
            self.io.tool_error(f"Unable to list files in git repo: {err}")
            self.io.tool_output("Is your git repo corrupted?")
            return []

        files = set()
        if commit:
            if commit in self.tree_files:
                files = self.tree_files[commit]
            else:
                try:
                    iterator = commit.tree.traverse()
                    while True:
                        try:
                            blob = next(iterator)
                            if blob.type == "blob":  # blob is a file
                                files.add(blob.path)
                        except IndexError:
                            self.io.tool_warning(f"GitRepo: read error skipping {blob.path}")
                            continue
                        except StopIteration:
                            break
                except ANY_GIT_ERROR as err:
                    self.git_repo_error = err
                    self.io.tool_error(f"Unable to list files in git repo: {err}")
                    self.io.tool_output("Is your git repo corrupted?")
                    return []
                files = set(self.normalize_path(path) for path in files)
                self.tree_files[commit] = set(files)

        # Add staged files
        index = self.repo.index
        staged_files = [path for path, _ in index.entries.keys()]
        files.update(self.normalize_path(path) for path in staged_files)

        res = [fname for fname in files if not self.ignored_file(fname)]

        return res

    def normalize_path(self, path):
        orig_path = path
        res = self.normalized_path.get(orig_path)
        if res:
            return res

        try:
            relative = Path(self.root).relative_to(Path(self.repo.workdir))
            path = str(PurePosixPath(relative) / PurePosixPath(path))
        except ValueError:
            path = str(Path(self.root) / path)
        self.normalized_path[orig_path] = path
        return path

    def refresh_aider_ignore(self):
        if not self.aider_ignore_file:
            return

        current_time = time.time()
        if current_time - self.aider_ignore_last_check < 1:
            return

        self.aider_ignore_last_check = current_time

        if not self.aider_ignore_file.is_file():
            return

        mtime = self.aider_ignore_file.stat().st_mtime
        if mtime != self.aider_ignore_ts:
            self.aider_ignore_ts = mtime
            self.ignore_file_cache = {}
            lines = self.aider_ignore_file.read_text().splitlines()
            self.aider_ignore_spec = pathspec.PathSpec.from_lines(
                pathspec.patterns.GitWildMatchPattern,
                lines,
            )

    def git_ignored_file(self, path):
        if not self.repo:
            return
        try:
            if self.repo.ignored(path):
                return True
        except ANY_GIT_ERROR:
            return False

    def ignored_file(self, fname):
        self.refresh_aider_ignore()

        if fname in self.ignore_file_cache:
            return self.ignore_file_cache[fname]

        result = self.ignored_file_raw(fname)
        self.ignore_file_cache[fname] = result
        return result

    def ignored_file_raw(self, fname):
        if self.subtree_only:
            try:
                fname_path = Path(self.normalize_path(fname))
                cwd_path = Path.cwd().resolve().relative_to(Path(self.root).resolve())
            except ValueError:
                # Issue #1524
                # ValueError: 'C:\\dev\\squid-certbot' is not in the subpath of
                # 'C:\\dev\\squid-certbot'
                # Clearly, fname is not under cwd... so ignore it
                return True

            if cwd_path not in fname_path.parents and fname_path != cwd_path:
                return True

        if not self.aider_ignore_file or not self.aider_ignore_file.is_file():
            return False

        try:
            fname = self.normalize_path(fname)
        except ValueError:
            return True

        return self.aider_ignore_spec.match_file(fname)

    def path_in_repo(self, path):
        if not self.repo:
            return
        if not path:
            return

        tracked_files = set(self.get_tracked_files())
        return self.normalize_path(path) in tracked_files

    def abs_root_path(self, path):
        res = Path(self.root) / path
        return utils.safe_abs_path(res)

    def get_dirty_files(self):
        """
        Returns a list of all files which are dirty (not committed), either staged or in the working
        directory.
        """
        dirty_files = set()

        # Get staged files
        staged_files = self.repo.git.diff("--name-only", "--cached").splitlines()
        dirty_files.update(staged_files)

        # Get unstaged files
        unstaged_files = self.repo.git.diff("--name-only").splitlines()
        dirty_files.update(unstaged_files)

        return list(dirty_files)

    def is_dirty(self, path=None):
        if path and not self.path_in_repo(path):
            return True

        return self.repo.is_dirty(path=path)

    def get_head_commit(self):
        try:
            return self.repo.head.commit
        except (ValueError,) + ANY_GIT_ERROR:
            return None

    def get_head_commit_sha(self, short=False):
        commit = self.get_head_commit()
        if not commit:
            return
        if short:
            return commit.hex[:7]
        return commit.hex

    def get_head_commit_message(self, default=None):
        commit = self.get_head_commit()
        if not commit:
            return default
        return commit.message
