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
    pygit2 = None
    ANY_GIT_ERROR = []

import pathspec

from aider import prompts
from aider.utils import safe_abs_path

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
        if fnames is None:
            fnames = []
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

        # Initialize a new repository if git_dname is provided and no existing repo is found
        if git_dname and not os.path.exists(os.path.join(git_dname, '.git')):
            try:
                self.repo = pygit2.init_repository(git_dname, initial_head='main')
                self.root = safe_abs_path(self.repo.workdir)
                if io:
                    io.tool_output(f"Initialized new git repository in {git_dname}")
                
                # Set up basic git config if not already set
                if 'user.name' not in self.repo.config:
                    self.repo.config['user.name'] = 'Aider User'
                if 'user.email' not in self.repo.config:
                    self.repo.config['user.email'] = 'aider@example.com'
                    
                if aider_ignore_file:
                    self.aider_ignore_file = Path(aider_ignore_file)
                return
            except ANY_GIT_ERROR as err:
                if io:
                    io.tool_error(f"Error initializing git repository: {err}")
                self.repo = None
                self.root = os.path.abspath(git_dname)
                if aider_ignore_file:
                    self.aider_ignore_file = Path(aider_ignore_file)
                return

        # Try to find an existing repository
        repo_path = None
        for fname in check_fnames:
            fname = Path(fname).resolve()
            if not fname.exists() and fname.parent.exists():
                fname = fname.parent

            try:
                repo_path = pygit2.discover_repository(str(fname))
                if repo_path:
                    break
            except ANY_GIT_ERROR:
                continue

        if not repo_path:
            # No git repository found
            self.repo = None
            self.root = os.path.abspath(git_dname) if git_dname else os.getcwd()
        else:
            try:
                self.repo = pygit2.Repository(repo_path)
                self.root = safe_abs_path(self.repo.workdir)
            except ANY_GIT_ERROR as err:
                if io:
                    io.tool_error(f"Error opening git repository: {err}")
                self.repo = None
                self.root = os.path.abspath(git_dname) if git_dname else os.getcwd()

        if aider_ignore_file:
            self.aider_ignore_file = Path(aider_ignore_file)




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
            index = self.repo.index
            if fnames:
                for fname in fnames:
                    try:
                        rel_path = os.path.relpath(fname, self.root)
                        index.add(rel_path)
                    except (ValueError, pygit2.GitError) as err:
                        self.io.tool_error(f"Unable to add {fname}: {err}")
            else:
                index.add_all()

            index.write()
            tree = index.write_tree()
            signature = pygit2.Signature(f"{original_user_name} (aider)", self.repo.config["user.email"])
            parents = [self.repo.head.target] if not self.repo.head_is_unborn else []
            commit_id = self.repo.create_commit('HEAD', signature, signature, full_commit_message, tree, parents)
            commit_hash = commit_id.hex[:7]
            self.io.tool_output(f"Commit {commit_hash} {commit_message}", bold=True)
            return commit_hash, commit_message
        except ANY_GIT_ERROR as err:
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
        if not self.repo:
            return None
        try:
            return os.path.relpath(self.repo.path, os.getcwd())
        except (ValueError, OSError):
            return self.repo.path

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
        if not fnames:
            fnames = []

        diffs = ""
        for fname in fnames:
            if not self.path_in_repo(fname):
                diffs += f"Added {fname}\n"

        try:
            # Check if the branch has any commits
            current_branch_has_commits = False
            try:
                current_branch_has_commits = not self.repo.head_is_unborn
            except ANY_GIT_ERROR:
                pass

            # Get the index
            index = self.repo.index
            index_tree = index.write_tree()
            index_obj = self.repo.get(index_tree)

            if current_branch_has_commits:
                head = self.repo.revparse_single('HEAD')
                
                # Get diff between HEAD and index (staged changes)
                diff_head_index = self.repo.diff(head, index_obj, paths=fnames)
                index_diffs = diff_head_index.patch
                diffs += index_diffs
                
                # Get diff between index and working directory
                diff_index_workdir = self.repo.diff_tree_to_workdir(index_obj, paths=fnames)
                working_diffs = diff_index_workdir.patch
                diffs += working_diffs
            else:
                # For repos with no commits yet, manually create diffs
                for fname in fnames or self.repo.status().keys():
                    try:
                        path = Path(os.path.join(self.root, fname))
                        if path.exists():
                            current_content = path.read_text()
                            # Check if file is in index
                            try:
                                index_entry = index[fname]
                                blob = self.repo[index_entry.id]
                                index_content = blob.data.decode('utf-8')
                                
                                # Show as new file in index
                                diffs += f"diff --git a/{fname} b/{fname}\n"
                                diffs += f"new file mode 100644\n"
                                diffs += f"--- /dev/null\n"
                                diffs += f"+++ b/{fname}\n"
                                diffs += f"@@ -0,0 +1,1 @@\n"
                                diffs += f"+{index_content.strip()}\n"
                                
                                # If working dir is different from index
                                if current_content != index_content:
                                    diffs += f"\ndiff --git a/{fname} b/{fname}\n"
                                    diffs += f"--- a/{fname}\n"
                                    diffs += f"+++ b/{fname}\n"
                                    diffs += f"@@ -1,1 +1,1 @@\n"
                                    diffs += f"-{index_content.strip()}\n"
                                    diffs += f"+{current_content.strip()}\n"
                            except (KeyError, ValueError):
                                # File not in index but in working dir
                                diffs += f"diff --git a/{fname} b/{fname}\n"
                                diffs += f"new file mode 100644\n"
                                diffs += f"--- /dev/null\n"
                                diffs += f"+++ b/{fname}\n"
                                diffs += f"@@ -0,0 +1,1 @@\n"
                                diffs += f"+{current_content.strip()}\n"
                    except Exception as e:
                        self.io.tool_error(f"Error processing file {fname}: {e}")

            return diffs
        except ANY_GIT_ERROR as err:
            self.io.tool_error(f"Unable to diff: {err}")
            return ""  # Return empty string instead of None

    def diff_commits(self, pretty, from_commit, to_commit):
        from_commit_obj = self.repo.revparse_single(from_commit)
        to_commit_obj = self.repo.revparse_single(to_commit)
        diff = self.repo.diff_tree_to_tree(from_commit_obj.tree, to_commit_obj.tree)
        return diff.patch

    def get_tracked_files(self):
        if not self.repo:
            return []

        try:
            # Check if the repository has any commits
            if self.repo.head_is_unborn:
                commit = None
            else:
                commit = self.repo.head.peel(pygit2.Commit)
        except (ValueError, pygit2.GitError) as err:
            commit = None
        except ANY_GIT_ERROR as err:
            self.git_repo_error = err
            if self.io:
                self.io.tool_error(f"Unable to list files in git repo: {err}")
                self.io.tool_output("Is your git repo corrupted?")
            return []

        files = set()
        if commit:
            if commit.id in self.tree_files:
                files = self.tree_files[commit.id]
            else:
                try:
                    # Traverse the tree to find all files
                    def traverse_tree(tree, path=""):
                        for entry in tree:
                            entry_path = path + entry.name if not path else f"{path}/{entry.name}"
                            if entry.type_str == "tree":
                                # It's a directory, recurse
                                subtree = self.repo[entry.id]
                                traverse_tree(subtree, entry_path)
                            elif entry.type_str == "blob":
                                # It's a file
                                files.add(entry_path)
                    
                    traverse_tree(commit.tree)
                except ANY_GIT_ERROR as err:
                    self.git_repo_error = err
                    if self.io:
                        self.io.tool_error(f"Unable to list files in git repo: {err}")
                        self.io.tool_output("Is your git repo corrupted?")
                    return []
                files = set(self.normalize_path(path) for path in files)
                self.tree_files[commit.id] = set(files)

        # Add staged files
        try:
            index = self.repo.index
            index.read()
            staged_files = [entry.path for entry in index]
            files.update(self.normalize_path(path) for path in staged_files)
        except ANY_GIT_ERROR as err:
            if self.io:
                self.io.tool_error(f"Unable to read git index: {err}")

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
            return False
        try:
            if self.repo.path_is_ignored(path):
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
        return safe_abs_path(res)

    def get_dirty_files(self):
        """
        Returns a list of all files which are dirty (not committed), either staged or in the working
        directory.
        """
        if not self.repo:
            return []
        return list(self.repo.status().keys())

    def is_dirty(self, path=None):
        if not self.repo:
            return False
            
        if path and not self.path_in_repo(path):
            return True

        # Check if repo has uncommitted changes
        if not path:
            return bool(self.repo.status())
        
        # Check if specific path is dirty
        status = self.repo.status()
        rel_path = os.path.relpath(path, self.root) if path else None
        return status.get(rel_path, 0) != 0

    def get_head_commit(self):
        if not self.repo:
            return None
        try:
            if self.repo.head_is_unborn:
                return None
            return self.repo.head.peel(pygit2.Commit)
        except (ValueError,) + ANY_GIT_ERROR:
            return None

    def get_head_commit_sha(self, short=False):
        commit = self.get_head_commit()
        if not commit:
            return None
        if short:
            return commit.id.hex[:7]
        return commit.id.hex

    def get_head_commit_message(self, default=None):
        commit = self.get_head_commit()
        if not commit:
            return default
        return commit.message
