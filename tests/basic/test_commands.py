import codecs
import os
import re
import shutil
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import TestCase, mock

import pygit2
import pyperclip

from aider.coders import Coder
from aider.commands import Commands, SwitchCoder
from aider.dump import dump  # noqa: F401
from aider.io import InputOutput
from aider.models import Model
from aider.repo import GitRepo
from aider.utils import ChdirTemporaryDirectory, GitTemporaryDirectory, make_repo


class TestCommands(TestCase):
    def setUp(self):
        self.original_cwd = os.getcwd()
        self.tempdir = tempfile.mkdtemp()
        os.chdir(self.tempdir)

        self.GPT35 = Model("gpt-3.5-turbo")

    def tearDown(self):
        os.chdir(self.original_cwd)
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_cmd_add(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Call the cmd_add method with 'foo.txt' and 'bar.txt' as a single string
        commands.cmd_add("foo.txt bar.txt")

        # Check if both files have been created in the temporary directory
        self.assertTrue(os.path.exists("foo.txt"))
        self.assertTrue(os.path.exists("bar.txt"))

    def test_cmd_copy(self):
        # Initialize InputOutput and Coder instances
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Add some assistant messages to the chat history
        coder.done_messages = [
            {"role": "assistant", "content": "First assistant message"},
            {"role": "user", "content": "User message"},
            {"role": "assistant", "content": "Second assistant message"},
        ]

        # Mock pyperclip.copy and io.tool_output
        with (
            mock.patch("pyperclip.copy") as mock_copy,
            mock.patch.object(io, "tool_output") as mock_tool_output,
        ):
            # Invoke the /copy command
            commands.cmd_copy("")

            # Assert pyperclip.copy was called with the last assistant message
            mock_copy.assert_called_once_with("Second assistant message")

            # Assert that tool_output was called with the expected preview
            expected_preview = (
                "Copied last assistant message to clipboard. Preview: Second assistant message"
            )
            mock_tool_output.assert_any_call(expected_preview)

    def test_cmd_copy_with_cur_messages(self):
        # Initialize InputOutput and Coder instances
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Add messages to done_messages and cur_messages
        coder.done_messages = [
            {"role": "assistant", "content": "First assistant message in done_messages"},
            {"role": "user", "content": "User message in done_messages"},
        ]
        coder.cur_messages = [
            {"role": "assistant", "content": "Latest assistant message in cur_messages"},
        ]

        # Mock pyperclip.copy and io.tool_output
        with (
            mock.patch("pyperclip.copy") as mock_copy,
            mock.patch.object(io, "tool_output") as mock_tool_output,
        ):
            # Invoke the /copy command
            commands.cmd_copy("")

            # Assert pyperclip.copy was called with the last assistant message in cur_messages
            mock_copy.assert_called_once_with("Latest assistant message in cur_messages")

            # Assert that tool_output was called with the expected preview
            expected_preview = (
                "Copied last assistant message to clipboard. Preview: Latest assistant message in"
                " cur_messages"
            )
            mock_tool_output.assert_any_call(expected_preview)

        # Reinitialize to reset state
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Add only user messages
        coder.done_messages = [
            {"role": "user", "content": "User message"},
        ]

        # Mock io.tool_error
        with mock.patch.object(io, "tool_error") as mock_tool_error:
            commands.cmd_copy("")
            # Assert tool_error was called indicating no assistant messages
            mock_tool_error.assert_called_once_with("No assistant messages found to copy.")

    def test_cmd_copy_pyperclip_exception(self):
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        coder.done_messages = [
            {"role": "assistant", "content": "Assistant message"},
        ]

        # Mock pyperclip.copy to raise an exception
        with (
            mock.patch(
                "pyperclip.copy", side_effect=pyperclip.PyperclipException("Clipboard error")
            ),
            mock.patch.object(io, "tool_error") as mock_tool_error,
        ):
            commands.cmd_copy("")

            # Assert that tool_error was called with the clipboard error message
            mock_tool_error.assert_called_once_with("Failed to copy to clipboard: Clipboard error")

    def test_cmd_add_bad_glob(self):
        # https://github.com/Aider-AI/aider/issues/293

        io = InputOutput(pretty=False, fancy_input=False, yes=False)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        commands.cmd_add("**.txt")

    def test_cmd_add_with_glob_patterns(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Create some test files
        with open("test1.py", "w") as f:
            f.write("print('test1')")
        with open("test2.py", "w") as f:
            f.write("print('test2')")
        with open("test.txt", "w") as f:
            f.write("test")

        # Initialize GitRepo and commit the files
        repo = GitRepo(io=io, fnames=[], git_dname=self.tempdir)
        repo.repo = pygit2.init_repository(self.tempdir, initial_head='main')
        repo.repo.config['user.name'] = 'Test User'
        repo.repo.config['user.email'] = 'testuser@example.com'
        for fname in ["test1.py", "test2.py", "test.txt"]:
            repo.repo.index.add(fname)
        repo.repo.index.write()
        author = pygit2.Signature("Test User", "testuser@example.com")
        repo.repo.create_commit(
            "HEAD",
            author,
            author,
            "Add test files",
            repo.repo.index.write_tree(),
            []
        )

        # Call the cmd_add method with a glob pattern
        commands.cmd_add("*.py")

        # Check if the Python files have been added to the chat session
        self.assertIn(str(Path("test1.py").resolve()), coder.abs_fnames)
        self.assertIn(str(Path("test2.py").resolve()), coder.abs_fnames)

        # Check if the text file has not been added to the chat session
        self.assertNotIn(str(Path("test.txt").resolve()), coder.abs_fnames)

    def test_cmd_add_no_match(self):
        # yes=False means we will *not* create the file when it is not found
        io = InputOutput(pretty=False, fancy_input=False, yes=False)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Call the cmd_add method with a non-existent file pattern
        commands.cmd_add("*.nonexistent")

        # Check if no files have been added to the chat session
        self.assertEqual(len(coder.abs_fnames), 0)

    def test_cmd_add_no_match_but_make_it(self):
        # yes=True means we will *will create the file when it is not found
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        fname = Path("[abc].nonexistent")

        # Call the cmd_add method with a non-existent file pattern
        commands.cmd_add(str(fname))

        # Check if the file has been added to the chat session
        self.assertEqual(len(coder.abs_fnames), 1)
        self.assertTrue(fname.exists())

    def test_cmd_add_drop_directory(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=False)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Create a directory and add files to it using pathlib
        Path("test_dir").mkdir()
        Path("test_dir/another_dir").mkdir()
        Path("test_dir/test_file1.txt").write_text("Test file 1")
        Path("test_dir/test_file2.txt").write_text("Test file 2")
        Path("test_dir/another_dir/test_file.txt").write_text("Test file 3")

        # Initialize GitRepo and commit the files
        repo = GitRepo(io=io, fnames=[], git_dname=self.tempdir)
        repo.repo = pygit2.init_repository(self.tempdir, initial_head='main')
        repo.repo.config['user.name'] = 'Test User'
        repo.repo.config['user.email'] = 'testuser@example.com'
        for fname in ["test_dir/test_file1.txt", "test_dir/test_file2.txt", "test_dir/another_dir/test_file.txt"]:
            repo.repo.index.add(fname)
        repo.repo.index.write()
        author = pygit2.Signature("Test User", "testuser@example.com")
        repo.repo.create_commit(
            "HEAD",
            author,
            author,
            "Add test directory files",
            repo.repo.index.write_tree(),
            []
        )

        # Call the cmd_add method with a directory and a specific file
        commands.cmd_add("test_dir test_dir/test_file2.txt")

        # Check if the files have been added to the chat session
        self.assertIn(str(Path("test_dir/test_file1.txt").resolve()), coder.abs_fnames)
        self.assertIn(str(Path("test_dir/test_file2.txt").resolve()), coder.abs_fnames)
        self.assertIn(str(Path("test_dir/another_dir/test_file.txt").resolve()), coder.abs_fnames)

        # Drop the 'another_dir' directory
        commands.cmd_drop("test_dir/another_dir")
        self.assertIn(str(Path("test_dir/test_file1.txt").resolve()), coder.abs_fnames)
        self.assertIn(str(Path("test_dir/test_file2.txt").resolve()), coder.abs_fnames)
        self.assertNotIn(
            str(Path("test_dir/another_dir/test_file.txt").resolve()), coder.abs_fnames
        )

        # Issue #139 /add problems when cwd != git_root

        # remember the proper abs path to this file
        abs_fname = str(Path("test_dir/another_dir/test_file.txt").resolve())

        # chdir to someplace other than git_root
        Path("side_dir").mkdir()
        os.chdir("side_dir")

        # add it via its git_root referenced name
        commands.cmd_add("test_dir/another_dir/test_file.txt")

        # it should be there, but was not in v0.10.0
        self.assertIn(abs_fname, coder.abs_fnames)

        # drop it via its git_root referenced name
        commands.cmd_drop("test_dir/another_dir/test_file.txt")

        # it should be removed now
        self.assertNotIn(abs_fname, coder.abs_fnames)

    def test_cmd_drop_with_glob_patterns(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Create test files in root and subdirectory
        subdir = Path("subdir")
        subdir.mkdir()
        (subdir / "subtest1.py").touch()
        (subdir / "subtest2.py").touch()

        Path("test1.py").touch()
        Path("test2.py").touch()
        Path("test3.txt").touch()

        # Initialize GitRepo and commit the files
        repo = GitRepo(io=io, fnames=[], git_dname=self.tempdir)
        repo.repo = pygit2.init_repository(self.tempdir, initial_head='main')
        repo.repo.config['user.name'] = 'Test User'
        repo.repo.config['user.email'] = 'testuser@example.com'
        for fname in ["test1.py", "test2.py", "test3.txt", "subdir/subtest1.py", "subdir/subtest2.py"]:
            repo.repo.index.add(fname)
        repo.repo.index.write()
        author = pygit2.Signature("Test User", "testuser@example.com")
        repo.repo.create_commit(
            "HEAD",
            author,
            author,
            "Add test patterns",
            repo.repo.index.write_tree(),
            []
        )

        # Add all Python files to the chat session
        commands.cmd_add("*.py")
        initial_count = len(coder.abs_fnames)
        self.assertEqual(initial_count, 2)  # Only root .py files should be added

        # Test dropping with glob pattern
        commands.cmd_drop("*2.py")
        self.assertIn(str(Path("test1.py").resolve()), coder.abs_fnames)
        self.assertNotIn(str(Path("test2.py").resolve()), coder.abs_fnames)
        self.assertEqual(len(coder.abs_fnames), initial_count - 1)

    def test_cmd_drop_without_glob(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Create test files
        test_files = ["file1.txt", "file2.txt", "file3.py"]
        for fname in test_files:
            Path(fname).touch()

        # Initialize GitRepo and commit the files
        repo = GitRepo(io=io, fnames=[], git_dname=self.tempdir)
        repo.repo = pygit2.init_repository(self.tempdir, initial_head='main')
        repo.repo.config['user.name'] = 'Test User'
        repo.repo.config['user.email'] = 'testuser@example.com'
        for fname in test_files:
            repo.repo.index.add(fname)
        repo.repo.index.write()
        author = pygit2.Signature("Test User", "testuser@example.com")
        repo.repo.create_commit(
            "HEAD",
            author,
            author,
            "Add individual files",
            repo.repo.index.write_tree(),
            []
        )

        # Add all files to the chat session
        for fname in test_files:
            commands.cmd_add(fname)

        initial_count = len(coder.abs_fnames)
        self.assertEqual(initial_count, 3)

        # Test dropping individual files without glob
        commands.cmd_drop("file1.txt")
        self.assertNotIn(str(Path("file1.txt").resolve()), coder.abs_fnames)
        self.assertIn(str(Path("file2.txt").resolve()), coder.abs_fnames)
        self.assertEqual(len(coder.abs_fnames), initial_count - 1)

        # Test dropping multiple files without glob
        commands.cmd_drop("file2.txt file3.py")
        self.assertNotIn(str(Path("file2.txt").resolve()), coder.abs_fnames)
        self.assertNotIn(str(Path("file3.py").resolve()), coder.abs_fnames)
        self.assertEqual(len(coder.abs_fnames), 0)

    def test_cmd_add_bad_encoding(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        from aider.coders import Coder

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        fname = "foo.bad"
        encoding = "utf-16"
        some_content_which_will_error_if_read_with_encoding_utf8 = "ÅÍÎÏ".encode(encoding)
        with open(fname, "wb") as f:
            f.write(some_content_which_will_error_if_read_with_encoding_utf8)

        commands.cmd_add("foo.bad")
        self.assertEqual(coder.abs_fnames, set())

    def test_cmd_git(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=True)

        with GitTemporaryDirectory() as tempdir:
            # Create a file in the temporary directory
            test_file_path = Path(tempdir) / "test.txt"
            test_file_path.write_text("test")

            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Initialize GitRepo and commit the file
            repo = GitRepo(io=io, fnames=[], git_dname=tempdir)
            repo.repo = pygit2.init_repository(tempdir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            repo.repo.index.add(str(test_file_path.relative_to(tempdir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Add test.txt",
                repo.repo.index.write_tree(),
                []
            )

            # Run the cmd_git method with the arguments "add test.txt" and "commit -a -m msg"
            commands.cmd_git("add test.txt")
            commands.cmd_git("commit -a -m msg")

            # Check if the file has been committed to the repository
            # Pygit2 does not have a 'listall' method. To list all files in the current tree:
            current_tree = repo.repo.head.peel().tree
            files_in_repo = [entry.name for entry in current_tree]
            self.assertIn("test.txt", files_in_repo)

    def test_cmd_tokens(self):
        # Initialize the Commands and InputOutput objects
        io = InputOutput(pretty=False, fancy_input=False, yes=True)

        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # Create and commit files
        filenames = ["foo.txt", "bar.txt"]
        for fname in filenames:
            file_path = Path(fname)
            file_path.touch()
            commands.cmd_add(fname)

        # Initialize GitRepo and commit the files
        repo = GitRepo(io=io, fnames=list(coder.abs_fnames), git_dname=self.tempdir)
        repo.repo = pygit2.init_repository(self.tempdir, initial_head='main')
        repo.repo.config['user.name'] = 'Test User'
        repo.repo.config['user.email'] = 'testuser@example.com'
        for fname in filenames:
            repo.repo.index.add(fname)
        repo.repo.index.write()
        author = pygit2.Signature("Test User", "testuser@example.com")
        repo.repo.create_commit(
            "HEAD",
            author,
            author,
            "Add foo and bar",
            repo.repo.index.write_tree(),
            []
        )

        # Redirect the standard output to an instance of io.StringIO
        stdout = StringIO()
        sys.stdout = stdout

        commands.cmd_tokens("")

        # Reset the standard output
        sys.stdout = sys.__stdout__

        # Get the console output
        console_output = stdout.getvalue()

        self.assertIn("foo.txt", console_output)
        self.assertIn("bar.txt", console_output)

    def test_cmd_add_from_subdir(self):
        # Initialize GitRepo and commit the files
        repo = GitRepo(InputOutput(), fnames=[], git_dname=".")
        repo.repo = pygit2.init_repository(".", initial_head='main')
        repo.repo.config['user.name'] = 'Test User'
        repo.repo.config['user.email'] = 'testuser@example.com'

        # Create three empty files and add them to the git repository
        filenames = ["one.py", "subdir/two.py", "anotherdir/three.py"]
        for filename in filenames:
            file_path = Path(filename)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.touch()
            repo.repo.index.add(str(file_path.relative_to(self.tempdir)))
        repo.repo.index.write()
        author = pygit2.Signature("Test User", "testuser@example.com")
        repo.repo.create_commit(
            "HEAD",
            author,
            author,
            "added",
            repo.repo.index.write_tree(),
            []
        )

        filenames = [str(Path(fn).resolve()) for fn in filenames]

        os.chdir("subdir")

        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        # this should get added
        commands.cmd_add(str(Path("anotherdir") / "three.py"))

        # this should add one.py
        commands.cmd_add("*.py")

        self.assertIn(filenames[0], coder.abs_fnames)
        self.assertNotIn(filenames[1], coder.abs_fnames)
        self.assertIn(filenames[2], coder.abs_fnames)

    def test_cmd_add_from_subdir_again(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            Path("side_dir").mkdir()
            os.chdir("side_dir")

            # add a file that is in the side_dir
            temp_file = Path("temp.txt")
            temp_file.touch()

            # this was blowing up with GitCommandError, per:
            # https://github.com/Aider-AI/aider/issues/201
            commands.cmd_add("temp.txt")

            # Verify the file was added
            self.assertIn(str(temp_file.resolve()), coder.abs_fnames)

    def test_cmd_commit(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            fname = "test.txt"
            file_path = Path(repo_dir) / fname
            with open(file_path, "w") as f:
                f.write("test")
            repo.repo.index.add(str(Path(fname).relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "initial",
                repo.repo.index.write_tree(),
                []
            )

            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Modify the file to make it dirty
            with open(file_path, "w") as f:
                f.write("new")

            # Check if repo is dirty
            status = repo.repo.status()
            is_dirty = any(status.values())
            self.assertTrue(is_dirty)

            # Commit the changes
            commit_message = "Test commit message"
            commands.cmd_commit(commit_message)

            # Check if repo is clean after commit
            status_after = repo.repo.status()
            is_dirty_after = any(status_after.values())
            self.assertFalse(is_dirty_after)

    def test_cmd_add_from_outside_root(self):
        with ChdirTemporaryDirectory() as tmp_dname:
            root = Path("root")
            root.mkdir()
            os.chdir(str(root))

            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            outside_file = Path(tmp_dname) / "outside.txt"
            outside_file.touch()

            # This should not be allowed!
            # https://github.com/Aider-AI/aider/issues/178
            commands.cmd_add("../outside.txt")

            self.assertEqual(len(coder.abs_fnames), 0)

    def test_cmd_add_from_outside_git(self):
        with ChdirTemporaryDirectory() as tmp_dname:
            root = Path("root")
            root.mkdir()
            os.chdir(str(root))

            make_repo()

            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            outside_file = Path(tmp_dname) / "outside.txt"
            outside_file.touch()

            # This should not be allowed!
            # It was blowing up with GitCommandError, per:
            # https://github.com/Aider-AI/aider/issues/178
            commands.cmd_add("../outside.txt")

            self.assertEqual(len(coder.abs_fnames), 0)

    def test_cmd_add_filename_with_special_chars(self):
        with ChdirTemporaryDirectory():
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            fname = Path("with[brackets].txt")
            fname.touch()

            commands.cmd_add(str(fname.resolve()))

            self.assertIn(str(fname.resolve()), coder.abs_fnames)

    def test_cmd_tokens_output(self):
        with GitTemporaryDirectory() as repo_dir:
            # Create a small repository with a few files
            (Path(repo_dir) / "file1.txt").write_text("Content of file 1")
            (Path(repo_dir) / "file2.py").write_text("print('Content of file 2')")
            (Path(repo_dir) / "subdir").mkdir()
            (Path(repo_dir) / "subdir" / "file3.md").write_text("# Content of file 3")

            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            for fname in ["file1.txt", "file2.py", "subdir/file3.md"]:
                repo.repo.index.add(fname)
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Initial commit",
                repo.repo.index.write_tree(),
                []
            )

            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(Model("claude-3-5-sonnet-20240620"), None, io)
            print(coder.get_announcements())
            commands = Commands(io, coder)

            commands.cmd_add("*.txt")

            # Capture the output of cmd_tokens
            original_tool_output = io.tool_output
            output_lines = []

            def capture_output(*args, **kwargs):
                output_lines.extend(args)
                original_tool_output(*args, **kwargs)

            io.tool_output = capture_output

            # Initialize GitRepo correctly
            commands.repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            commands.repo.repo = repo.repo

            # Run cmd_tokens
            commands.cmd_tokens("")

            # Restore original tool_output
            io.tool_output = original_tool_output

            # Check if the output includes repository map information
            repo_map_line = next((line for line in output_lines if "repository map" in line), None)
            self.assertIsNotNone(
                repo_map_line, "Repository map information not found in the output"
            )

            # Check if the output includes information about all added files
            self.assertTrue(any("file1.txt" in line for line in output_lines))

            # Check if the total tokens and remaining tokens are reported
            self.assertTrue(any("tokens total" in line for line in output_lines))
            self.assertTrue(any("tokens remaining" in line for line in output_lines))

    def test_cmd_add_dirname_with_special_chars_git(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            dname = Path("with[brackets]")
            dname.mkdir()
            fname = dname / "filename.txt"
            fname.touch()

            # Initialize GitRepo correctly
            repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            repo.repo.index.add(str(fname.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "init",
                repo.repo.index.write_tree(),
                []
            )

            commands.cmd_add(str(dname.resolve()))

            dump(coder.abs_fnames)
            # Update the assertion to match the pygit2.Repo's current tree
            # Since commands.cmd_add already added the files, ensure it's present
            self.assertIn(str(fname.resolve()), coder.abs_fnames)

    def test_cmd_add_abs_filename(self):
        with ChdirTemporaryDirectory():
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            fname = Path("file.txt")
            fname.touch()

            commands.cmd_add(str(fname.resolve()))

            self.assertIn(str(fname.resolve()), coder.abs_fnames)

    def test_cmd_add_quoted_filename(self):
        with ChdirTemporaryDirectory():
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            from aider.coders import Coder

            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            fname = Path("file with spaces.txt")
            fname.touch()

            commands.cmd_add(f'"{fname}"')

            self.assertIn(str(fname.resolve()), coder.abs_fnames)

    def test_cmd_add_existing_with_dirty_repo(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            files = ["one.txt", "two.txt"]
            for fname in files:
                Path(fname).touch()
                repo.repo.index.add(fname)
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "initial commit",
                repo.repo.index.write_tree(),
                []
            )

            try:
                commit_obj = repo.repo.head.target
                commit_hash = str(commit_obj)
                coder.aider_commit_hashes.add(commit_hash[:7])

                # Leave a dirty `git rm`
                repo.repo.index.remove("one.txt")
                repo.repo.index.write()

                io = InputOutput(pretty=False, fancy_input=False, yes=True)
                from aider.coders import Coder

                coder = Coder.create(self.GPT35, None, io)
                commands = Commands(io, coder)

                # Initialize GitRepo correctly
                commands.repo = repo

                # There's no reason this /add should trigger a commit
                commands.cmd_add("two.txt")

                # Check that the last commit is still present
                current_commit = str(repo.repo.head.target)
                self.assertEqual(commit_hash[:7], current_commit[:7])

                # Commit cleanup
                repo.repo.create_commit(
                    "HEAD",
                    author,
                    author,
                    "cleanup",
                    repo.repo.index.write_tree(),
                    [repo.repo.head.target]
                )

                # Verify no exception is raised
            except Exception as e:
                self.fail(f"Test failed due to unexpected exception: {e}")
            finally:
                del coder
                del commands
                del repo

    def test_cmd_save_and_load(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create some test files
            test_files = {
                "file1.txt": "Content of file 1",
                "file2.py": "print('Content of file 2')",
                "subdir/file3.md": "# Content of file 3",
            }

            for file_path, content in test_files.items():
                full_path = Path(repo_dir) / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content)

            # Add some files as editable and some as read-only
            commands.cmd_add("file1.txt file2.py")
            commands.cmd_read_only("subdir/file3.md")

            # Initialize GitRepo and commit the files
            repo = GitRepo(io=io, fnames=list(coder.abs_fnames), git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            for fname in ["file1.txt", "file2.py", "subdir/file3.md"]:
                repo.repo.index.add(fname)
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Initial commit",
                repo.repo.index.write_tree(),
                []
            )

            # Save the session to a file
            session_file = "test_session.txt"
            commands.cmd_save(session_file)

            # Verify the session file was created and contains the expected commands
            self.assertTrue(Path(session_file).exists())
            with open(session_file, encoding=io.encoding) as f:
                commands_text = f.read().splitlines()

                # Convert paths to absolute for comparison
                abs_file1 = str(Path("file1.txt").resolve())
                abs_file2 = str(Path("file2.py").resolve())
                abs_file3 = str(Path("subdir/file3.md").resolve())

                # Check each line for matching paths using os.path.samefile
                found_file1 = found_file2 = found_file3 = False
                for line in commands_text:
                    if line.startswith("/add "):
                        path = Path(line[5:].strip()).resolve()
                        if os.path.samefile(str(path), abs_file1):
                            found_file1 = True
                        elif os.path.samefile(str(path), abs_file2):
                            found_file2 = True
                    elif line.startswith("/read-only "):
                        path = Path(line[11:]).resolve()
                        if os.path.samefile(str(path), abs_file3):
                            found_file3 = True

                self.assertTrue(found_file1, "file1.txt not found in commands")
                self.assertTrue(found_file2, "file2.py not found in commands")
                self.assertTrue(found_file3, "file3.md not found in commands")

            # Clear the current session
            commands.cmd_reset("")
            self.assertEqual(len(coder.abs_fnames), 0)
            self.assertEqual(len(coder.abs_read_only_fnames), 0)

            # Load the session back
            commands.cmd_load(session_file)

            # Verify files were restored correctly
            added_files = {coder.get_rel_fname(f) for f in coder.abs_fnames}
            read_only_files = {coder.get_rel_fname(f) for f in coder.abs_read_only_fnames}

            self.assertEqual(added_files, {"file1.txt", "file2.py"})
            self.assertEqual(read_only_files, {"subdir/file3.md"})

            # Clean up
            Path(session_file).unlink()

    def test_cmd_save_and_load_with_external_file(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as external_file:
            external_file.write("External file content")
            external_file_path = external_file.name

        try:
            with GitTemporaryDirectory() as repo_dir:
                # Create a test file in the repo
                repo_file = Path(repo_dir) / "repo_file.txt"
                repo_file.write_text("Repo file content")
                io = InputOutput(pretty=False, fancy_input=False, yes=False)
                coder = Coder.create(self.GPT35, None, io)
                commands = Commands(io, coder)

                # Initialize GitRepo and commit the repo file
                repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
                repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
                repo.repo.config['user.name'] = 'Test User'
                repo.repo.config['user.email'] = 'testuser@example.com'
                repo.repo.index.add("repo_file.txt")
                repo.repo.index.write()
                author = pygit2.Signature("Test User", "testuser@example.com")
                repo.repo.create_commit(
                    "HEAD",
                    author,
                    author,
                    "Add repo_file.txt",
                    repo.repo.index.write_tree(),
                    []
                )

                # Test the /read-only command with an external file
                commands.cmd_read_only(external_file_path)

                # Check if the external file was added to abs_read_only_fnames
                real_external_file_path = os.path.realpath(external_file_path)
                self.assertTrue(
                    any(
                        os.path.samefile(real_external_file_path, fname)
                        for fname in coder.abs_read_only_fnames
                    )
                )

                # Test dropping the external read-only file
                commands.cmd_drop(Path(external_file_path).name)

                # Check if the file was removed from abs_read_only_fnames
                self.assertFalse(
                    any(
                        os.path.samefile(real_external_file_path, fname)
                        for fname in coder.abs_read_only_fnames
                    )
                )
        finally:
            os.unlink(external_file_path)

    def test_cmd_save_and_load_with_multiple_external_files(self):
        with (
            tempfile.NamedTemporaryFile(mode="w", delete=False) as external_file1,
            tempfile.NamedTemporaryFile(mode="w", delete=False) as external_file2,
        ):
            external_file1.write("External file 1 content")
            external_file2.write("External file 2 content")
            external_file1_path = external_file1.name
            external_file2_path = external_file2.name

        try:
            with GitTemporaryDirectory() as repo_dir:
                io = InputOutput(pretty=False, fancy_input=False, yes=True)
                coder = Coder.create(self.GPT35, None, io)
                commands = Commands(io, coder)

                # Create some test files in the repo
                test_files = {
                    "internal1.txt": "Content of internal file 1",
                    "internal2.txt": "Content of internal file 2",
                }

                for file_path, content in test_files.items():
                    full_path = Path(repo_dir) / file_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content)
                    commands.cmd_add(file_path)

                # Add external files as read-only
                commands.cmd_read_only(external_file1_path)
                commands.cmd_read_only(external_file2_path)

                # Save the session to a file
                session_file = "test_session.txt"
                commands.cmd_save(session_file)

                # Verify the session file was created and contains the expected commands
                self.assertTrue(Path(session_file).exists())
                with open(session_file, encoding=io.encoding) as f:
                    commands_text = f.read()
                    commands_text = re.sub(
                        r"/add +", "/add ", commands_text
                    )  # Normalize add command spaces
                    self.assertIn("/add internal1.txt", commands_text)
                    # Split commands and check each one
                    found_external1 = found_external2 = False
                    for line in commands_text.splitlines():
                        if line.startswith("/read-only "):
                            saved_path = line.split(" ", 1)[1]
                            if os.path.samefile(saved_path, external_file1_path):
                                found_external1 = True
                            elif os.path.samefile(saved_path, external_file2_path):
                                found_external2 = True

                    self.assertTrue(found_external1, f"No matching read-only command found for {external_file1_path}")
                    self.assertTrue(found_external2, f"No matching read-only command found for {external_file2_path}")

                # Clear the current session
                commands.cmd_reset("")
                self.assertEqual(len(coder.abs_fnames), 0)
                self.assertEqual(len(coder.abs_read_only_fnames), 0)

                # Load the session back
                commands.cmd_load(session_file)

                # Verify files were restored correctly
                added_files = {coder.get_rel_fname(f) for f in coder.abs_fnames}
                read_only_files = {coder.get_rel_fname(f) for f in coder.abs_read_only_fnames}

                self.assertEqual(added_files, {"internal1.txt", "internal2.txt"})
                self.assertTrue(
                    any(os.path.samefile(external_path, fname) for fname in read_only_files)
                    for external_path in [external_file1_path, external_file2_path]
                )

                # Clean up
                Path(session_file).unlink()

        finally:
            os.unlink(external_file1_path)
            os.unlink(external_file2_path)

    def test_cmd_read_only_with_image_file(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create a test image file
            test_file = Path(repo_dir) / "test_image.jpg"
            test_file.write_text("Mock image content")

            # Test with non-vision model
            commands.cmd_read_only(str(test_file))
            self.assertEqual(len(coder.abs_read_only_fnames), 0)

            # Test with vision model
            vision_model = Model("gpt-4-vision-preview")
            vision_coder = Coder.create(vision_model, None, io)
            vision_commands = Commands(io, vision_coder)

            # Initialize GitRepo correctly
            vision_commands.repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            vision_commands.repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            vision_commands.repo.repo.config['user.name'] = 'Test User'
            vision_commands.repo.repo.config['user.email'] = 'testuser@example.com'
            vision_commands.repo.repo.index.add("test_image.jpg")
            vision_commands.repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            vision_commands.repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "add image",
                vision_commands.repo.repo.index.write_tree(),
                []
            )

            # Invoke cmd_read_only for the image file
            vision_commands.cmd_read_only(str(test_file))
            self.assertEqual(len(vision_coder.abs_read_only_fnames), 1)
            self.assertTrue(
                any(
                    os.path.samefile(str(test_file.resolve()), fname)
                    for fname in vision_coder.abs_read_only_fnames
                )
            )

            # Add a dummy message to ensure format_messages() works
            vision_coder.cur_messages = [{"role": "user", "content": "Check the image"}]

            # Check that the image file appears in the messages
            messages = vision_coder.format_messages().all_messages()
            found_image = False
            for msg in messages:
                if msg.get("role") == "user" and "content" in msg:
                    content = msg["content"]
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                if "test_image.jpg" in item.get("text", ""):
                                    found_image = True
                                    break
            self.assertTrue(found_image, "Image file not found in messages to LLM")

    def test_cmd_read_only_with_glob_pattern(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create multiple test files
            test_files = ["test_file1.txt", "test_file2.txt", "other_file.txt"]
            for file_name in test_files:
                file_path = Path(repo_dir) / file_name
                file_path.write_text(f"Content of {file_name}")

            # Initialize GitRepo and commit the files
            repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            for file_name in test_files:
                repo.repo.index.add(file_name)
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Initial commit",
                repo.repo.index.write_tree(),
                []
            )

            # Test the /read-only command with a glob pattern
            commands.cmd_read_only("test_*.txt")

            # Check if only the matching files were added to abs_read_only_fnames
            self.assertEqual(len(coder.abs_read_only_fnames), 2)
            for file_name in ["test_file1.txt", "test_file2.txt"]:
                file_path = Path(repo_dir) / file_name
                self.assertTrue(
                    any(
                        os.path.samefile(str(file_path.resolve()), fname)
                        for fname in coder.abs_read_only_fnames
                    )
                )

            # Check that other_file.txt was not added
            other_file_path = Path(repo_dir) / "other_file.txt"
            self.assertFalse(
                any(
                    os.path.samefile(str(other_file_path), fname)
                    for fname in coder.abs_read_only_fnames
                )
            )

    def test_cmd_read_only_with_recursive_glob(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create a directory structure with files
            (Path(repo_dir) / "subdir").mkdir()
            test_files = ["test_file1.txt", "subdir/test_file2.txt", "subdir/other_file.txt"]
            for file_name in test_files:
                file_path = Path(repo_dir) / file_name
                file_path.write_text(f"Content of {file_name}")

            # Initialize GitRepo and commit the files
            repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            for file_name in test_files:
                repo.repo.index.add(file_name)
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Initial commit",
                repo.repo.index.write_tree(),
                []
            )

            # Test the /read-only command with a recursive glob pattern
            commands.cmd_read_only("**/*.txt")

            # Check if all .txt files were added to abs_read_only_fnames
            self.assertEqual(len(coder.abs_read_only_fnames), 3)
            for file_name in test_files:
                file_path = Path(repo_dir) / file_name
                self.assertTrue(
                    any(
                        os.path.samefile(str(file_path), fname)
                        for fname in coder.abs_read_only_fnames
                    )
                )

    def test_cmd_read_only_with_nonexistent_glob(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Initialize GitRepo and commit the files
            repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            # Test the /read-only command with a non-existent glob pattern
            with mock.patch.object(io, "tool_error") as mock_tool_error:
                commands.cmd_read_only(str(Path(repo_dir) / "nonexistent*.txt"))

            # Check if the appropriate error message was displayed
            mock_tool_error.assert_called_once_with(
                f"No matches found for: {Path(repo_dir) / 'nonexistent*.txt'}"
            )

            # Ensure no files were added to abs_read_only_fnames
            self.assertEqual(len(coder.abs_read_only_fnames), 0)

    def test_cmd_undo_with_dirty_files_not_in_last_commit(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            other_path = Path(repo_dir) / "other_file.txt"
            other_path.write_text("other content")
            repo.repo.index.add("other_file.txt")
            repo.repo.index.write()

            # Create and commit a file
            filename = "test_file.txt"
            file_path = Path(repo_dir) / filename
            file_path.write_text("first content")
            repo.repo.index.add(str(file_path.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "first commit",
                repo.repo.index.write_tree(),
                []
            )

            # Modify and commit again
            file_path.write_text("second content")
            repo.repo.index.add(str(file_path.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "second commit",
                repo.repo.index.write_tree(),
                [repo.repo.head.target]
            )

            # Store the commit hash
            last_commit_hash = str(repo.repo.head.target)
            coder.aider_commit_hashes.add(last_commit_hash[:7])

            # Modify the file to make it dirty
            file_path.write_text("dirty content")

            # Initialize GitRepo correctly
            commands.repo = repo

            # Attempt to undo the last commit
            commands.cmd_undo("")

            # Check that the last commit is still present
            current_commit = str(repo.repo.head.target)
            self.assertEqual(last_commit_hash[:7], current_commit[:7])

            # Put back the initial content (so it's not dirty now)
            file_path.write_text("second content")
            other_path.write_text("dirty content")

            # Attempt to undo again
            commands.cmd_undo("")

            # Check that the commit has been undone
            new_commit = str(repo.repo.head.target)
            self.assertNotEqual(last_commit_hash[:7], new_commit[:7])

            # Verify that the file content has been reverted
            self.assertEqual(file_path.read_text(), "first content")
            self.assertEqual(other_path.read_text(), "dirty content")

            del coder
            del commands
            del repo

    def test_cmd_undo_with_newly_committed_file(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Put in a random first commit
            filename = "first_file.txt"
            file_path = Path(repo_dir) / filename
            file_path.write_text("new file content")
            repo.repo.index.add(str(file_path.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Add new file",
                repo.repo.index.write_tree(),
                []
            )

            # Create and commit a new file
            filename = "new_file.txt"
            file_path = Path(repo_dir) / filename
            file_path.write_text("new file content")
            repo.repo.index.add(str(file_path.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Add new file",
                repo.repo.index.write_tree(),
                [repo.repo.head.target]
            )

            # Store the commit hash
            last_commit_hash = str(repo.repo.head.target)
            coder.aider_commit_hashes.add(last_commit_hash[:7])

            # Attempt to undo the last commit, should refuse
            commands.cmd_undo("")

            # Check that the last commit was not undone
            self.assertEqual(last_commit_hash[:7], str(repo.repo.head.target)[:7])
            self.assertTrue(file_path.exists())

            del coder
            del commands
            del repo

    def test_cmd_undo_on_first_commit(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create and commit a new file
            filename = "new_file.txt"
            file_path = Path(repo_dir) / filename
            file_path.write_text("new file content")
            repo.repo.index.add(str(file_path.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Add new file",
                repo.repo.index.write_tree(),
                []
            )

            # Store the commit hash
            last_commit_hash = str(repo.repo.head.target)
            coder.aider_commit_hashes.add(last_commit_hash[:7])

            # Attempt to undo the last commit
            commands.cmd_undo("")

            # Check that the commit is still present
            self.assertEqual(last_commit_hash[:7], str(repo.repo.head.target)[:7])
            self.assertTrue(file_path.exists())

            del coder
            del commands
            del repo

    def test_cmd_add_gitignored_file(self):
        with GitTemporaryDirectory() as repo_dir:
            # Create a .gitignore file
            gitignore = Path(repo_dir) / ".gitignore"
            gitignore.write_text("*.ignored\n")

            # Create a file that matches the gitignore pattern
            ignored_file = Path(repo_dir) / "test.ignored"
            ignored_file.write_text("This should be ignored")

            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Initialize GitRepo and commit .gitignore
            repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            repo.repo.index.add(".gitignore")
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Add .gitignore",
                repo.repo.index.write_tree(),
                []
            )

            # Try to add the ignored file
            commands.cmd_add(str(ignored_file))

            # Verify the file was not added
            self.assertEqual(len(coder.abs_fnames), 0)

    def test_cmd_add_aiderignored_file(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            fname1 = "ignoreme1.txt"
            fname2 = "ignoreme2.txt"
            fname3 = "dir/ignoreme3.txt"

            Path(fname2).touch()
            repo.repo.index.add(str(Path(fname2).relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "initial",
                repo.repo.index.write_tree(),
                []
            )

            aignore = Path(repo_dir) / ".aiderignore"
            aignore.write_text(f"{fname1}\n{fname2}\ndir\n")

            io = InputOutput(yes=True)

            fnames = [fname1, fname2]
            repo = GitRepo(
                io=io,
                fnames=fnames,
                git_dname=repo_dir,
                aider_ignore_file=str(aignore),
            )
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            coder = Coder.create(
                self.GPT35,
                None,
                io,
                fnames=fnames,
                repo=repo,
            )
            commands = Commands(io, coder)

            commands.cmd_add(f"{fname1} {fname2} {fname3}")

            self.assertNotIn(str(Path(fname1).resolve()), coder.abs_fnames)
            self.assertNotIn(str(Path(fname2).resolve()), coder.abs_fnames)
            self.assertNotIn(str(Path(fname3).resolve()), coder.abs_fnames)

    def test_cmd_save_and_load_bulk_conversion(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create and add some test files
            test_files = ["test1.txt", "test2.txt", "test3.txt"]
            for fname in test_files:
                file_path = Path(repo_dir) / fname
                file_path.write_text(f"Content of {fname}")

            # Initialize GitRepo and commit the files
            repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            for fname in test_files:
                repo.repo.index.add(fname)
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Initial commit",
                repo.repo.index.write_tree(),
                []
            )

            # Add some files as editable and some as read-only
            commands.cmd_add("test1.txt test2.txt test3.txt")
            commands.cmd_read_only("test1.txt test2.txt test3.txt")

            # Verify files are in read-only mode
            self.assertEqual(len(coder.abs_fnames), 0)
            self.assertEqual(len(coder.abs_read_only_fnames), 3)

            # Check specific files
            for fname in test_files:
                abs_path = Path(repo_dir) / fname
                self.assertTrue(
                    any(
                        os.path.samefile(str(abs_path), fname_ro)
                        for fname_ro in coder.abs_read_only_fnames
                    )
                )

    def test_cmd_read_only_with_multiple_files(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create multiple test files
            test_files = ["test_file1.txt", "test_file2.txt", "test_file3.txt"]
            for file_name in test_files:
                file_path = Path(repo_dir) / file_name
                file_path.write_text(f"Content of {file_name}")

            # Initialize GitRepo and commit the files
            repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'
            for file_name in test_files:
                repo.repo.index.add(file_name)
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Initial commit",
                repo.repo.index.write_tree(),
                []
            )

            # Test the /read-only command with multiple files
            commands.cmd_read_only("test_file1.txt test_file2.txt test_file3.txt")

            # Check if all test files were added to abs_read_only_fnames
            for file_name in test_files:
                file_path = Path(repo_dir) / file_name
                self.assertTrue(
                    any(
                        os.path.samefile(str(file_path), fname)
                        for fname in coder.abs_read_only_fnames
                    )
                )

            # Test dropping all read-only files
            commands.cmd_drop("test_file1.txt test_file2.txt test_file3.txt")

            # Check if all files were removed from abs_read_only_fnames
            self.assertEqual(len(coder.abs_read_only_fnames), 0)

    def test_cmd_read_only_with_tilde_path(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=False)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create a test file in the user's home directory
            home_dir = os.path.expanduser("~")
            test_file = Path(home_dir) / "test_read_only_file.txt"
            test_file.write_text("Test content")

            try:
                # Initialize GitRepo and commit the file
                repo = GitRepo(io=io, fnames=[], git_dname=repo_dir)
                repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
                repo.repo.config['user.name'] = 'Test User'
                repo.repo.config['user.email'] = 'testuser@example.com'
                repo.repo.index.add("test_read_only_file.txt")
                repo.repo.index.write()
                author = pygit2.Signature("Test User", "testuser@example.com")
                repo.repo.create_commit(
                    "HEAD",
                    author,
                    author,
                    "Add test_read_only_file.txt",
                    repo.repo.index.write_tree(),
                    []
                )

                # Test the /read-only command with a path in the user's home directory
                relative_path = os.path.join("~", "test_read_only_file.txt")
                commands.cmd_read_only(relative_path)

                # Check if the file was added to abs_read_only_fnames
                self.assertTrue(
                    any(
                        os.path.samefile(str(test_file.resolve()), fname)
                        for fname in coder.abs_read_only_fnames
                    )
                )

                # Test dropping the read-only file
                commands.cmd_drop(relative_path)

                # Check if the file was removed from abs_read_only_fnames
                self.assertEqual(len(coder.abs_read_only_fnames), 0)
            finally:
                # Clean up: remove the test file from the home directory
                try:
                    test_file.unlink()
                except FileNotFoundError:
                    pass

    def test_cmd_diff(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create and commit a file
            filename = "test_file.txt"
            file_path = Path(repo_dir) / filename
            file_path.write_text("Initial content\n")
            repo.repo.index.add(str(file_path.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Initial commit",
                repo.repo.index.write_tree(),
                []
            )

            # Modify the file to make it dirty
            file_path.write_text("Modified content")

            # Mock repo.get_commit_message to return a canned commit message
            with mock.patch.object(
                coder.repo, "get_commit_message", return_value="Canned commit message"
            ):
                # Run cmd_commit
                commands.cmd_commit()

                # Capture the output of cmd_diff
                with mock.patch("builtins.print") as mock_print:
                    commands.cmd_diff("")

                # Check if the diff output is correct
                mock_print.assert_called_with(mock.ANY)
                diff_output = mock_print.call_args[0][0]
                self.assertIn("-Initial content", diff_output)
                self.assertIn("+Modified content", diff_output)

                # Modify the file again
                file_path.write_text("Further modified content")

                # Run cmd_commit again
                commands.cmd_commit()

                # Capture the output of cmd_diff
                with mock.patch("builtins.print") as mock_print:
                    commands.cmd_diff("")

                # Check if the diff output is correct
                mock_print.assert_called_with(mock.ANY)
                diff_output = mock_print.call_args[0][0]
                self.assertIn("-Modified content", diff_output)
                self.assertIn("+Further modified content", diff_output)

                # Modify the file a third time
                file_path.write_text("Final modified content")

                # Run cmd_commit again
                commands.cmd_commit()

                # Capture the output of cmd_diff
                with mock.patch("builtins.print") as mock_print:
                    commands.cmd_diff("")

                # Check if the diff output is correct
                mock_print.assert_called_with(mock.ANY)
                diff_output = mock_print.call_args[0][0]
                self.assertIn("-Further modified content", diff_output)
                self.assertIn("+Final modified content", diff_output)

    def test_cmd_ask(self):
        io = InputOutput(pretty=False, fancy_input=False, yes=True)
        coder = Coder.create(self.GPT35, None, io)
        commands = Commands(io, coder)

        question = "What is the meaning of life?"
        canned_reply = "The meaning of life is 42."

        with mock.patch("aider.coders.Coder.run") as mock_run:
            mock_run.return_value = canned_reply

            with self.assertRaises(SwitchCoder):
                commands.cmd_ask(question)

            mock_run.assert_called_once_with(question)

    def test_cmd_lint_with_dirty_file(self):
        with GitTemporaryDirectory() as repo_dir:
            repo = GitRepo(io=None, fnames=[], git_dname=repo_dir)
            repo.repo = pygit2.init_repository(repo_dir, initial_head='main')
            repo.repo.config['user.name'] = 'Test User'
            repo.repo.config['user.email'] = 'testuser@example.com'

            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create and commit a file
            filename = "test_file.py"
            file_path = Path(repo_dir) / filename
            file_path.write_text("def hello():\n    print('Hello, World!')\n")
            repo.repo.index.add(str(file_path.relative_to(repo_dir)))
            repo.repo.index.write()
            author = pygit2.Signature("Test User", "testuser@example.com")
            repo.repo.create_commit(
                "HEAD",
                author,
                author,
                "Add test_file.py",
                repo.repo.index.write_tree(),
                []
            )

            # Modify the file to make it dirty
            file_path.write_text("def hello():\n    print('Hello, World!')\n\n# Dirty line\n")

            # Mock the linter.lint method
            with mock.patch.object(coder.linter, "lint") as mock_lint:
                # Set up the mock to return an empty string (no lint errors)
                mock_lint.return_value = ""

                # Run cmd_lint
                commands.cmd_lint()

                # Check if the linter was called with a filename string
                # whose Path().name matches the expected filename
                mock_lint.assert_called_once()
                called_arg = mock_lint.call_args[0][0]
                self.assertEqual(Path(called_arg).name, filename)

            # Verify that the file is still dirty after linting
            status = repo.repo.status(str(file_path.relative_to(repo_dir)))
            is_dirty = bool(status)
            self.assertTrue(is_dirty)

    def test_cmd_reset(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Add some files to the chat
            file1 = Path(repo_dir) / "file1.txt"
            file2 = Path(repo_dir) / "file2.txt"
            file1.write_text("Content of file 1")
            file2.write_text("Content of file 2")
            commands.cmd_add(f"{file1} {file2}")

            # Add some messages to the chat history
            coder.cur_messages = [{"role": "user", "content": "Test message 1"}]
            coder.done_messages = [{"role": "assistant", "content": "Test message 2"}]

            # Run the reset command
            commands.cmd_reset("")

            # Check that all files have been dropped
            self.assertEqual(len(coder.abs_fnames), 0)
            self.assertEqual(len(coder.abs_read_only_fnames), 0)

            # Check that the chat history has been cleared
            self.assertEqual(len(coder.cur_messages), 0)
            self.assertEqual(len(coder.done_messages), 0)

            # Verify that the files still exist in the repository
            self.assertTrue(file1.exists())
            self.assertTrue(file2.exists())

            del coder
            del commands

    def test_cmd_load_with_switch_coder(self):
        with GitTemporaryDirectory() as repo_dir:
            io = InputOutput(pretty=False, fancy_input=False, yes=True)
            coder = Coder.create(self.GPT35, None, io)
            commands = Commands(io, coder)

            # Create a temporary file with commands
            commands_file = Path(repo_dir) / "test_commands.txt"
            commands_file.write_text("/ask Tell me about the code\n/model gpt-4\n")

            # Mock run to raise SwitchCoder for /ask and /model
            def mock_run(cmd):
                if cmd.startswith(("/ask", "/model")):
                    raise SwitchCoder()
                return None

            with mock.patch.object(commands, "run", side_effect=mock_run):
                # Capture tool_error output
                with mock.patch.object(io, "tool_error") as mock_tool_error:
                    commands.cmd_load(str(commands_file))

                    # Check that appropriate error messages were shown
                    mock_tool_error.assert_any_call(
                        "Command '/ask Tell me about the code' is only supported in interactive"
                        " mode, skipping."
                    )
                    mock_tool_error.assert_any_call(
                        "Command '/model gpt-4' is only supported in interactive mode, skipping."
                    )
