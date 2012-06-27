import os
import subprocess

import fileutils


class CmdError(RuntimeError):
    def __init__(self, msg):
        RuntimeError.__init__(self, msg)

    

class AtomicCmd:
    """Executes a command, only moving resulting files to the destination
    directory if the command was succesful. This helps prevent the 
    accidential use of partial files in downstream analysis, and eases 
    restarting of a pipeline following errors (no cleanup)."""
    PIPE = subprocess.PIPE

    def __init__(self, destination, command, stdin = None, stdout = None, stderr = None, **kwargs):
        self.proc  = None
        self._cmd  = command
        self._dest = destination
        self._temp = None
        
        # Fill the in_files and out_files dictionaries 
        # in_files = jars, fasta, bams, etc. Prefix = "IN_"
        # out_files = files generated by the command. Prefix = "OUT_"
        self._in_files, self._out_files = {}, {}
        self._group_io_by_prefix(kwargs)

        self._stdin  = stdin
        self._stderr = stderr
        self._stdout = stdout
        self._handles = {}

        if type(stdin) is str:
            self._in_files["PIPE_STDIN"] = stdin
        if type(stdout) is str:
            self._out_files["PIPE_STDOUT"] = stdout
        if (type(stderr) is str) and (stdout != stderr):
            self._out_files["PIPE_STDERR"] = stderr

        self._temp_files = None
        self._final_files = self._generate_filenames(root = destination)


    def run(self, temp):
        """Runs the given command, saving files in the specified temp folder. To 
        move files to their final destination, call commit(). Note that in contexts
        where the *Cmds classes are used, this function may block."""
        assert self.executable_exists()
        assert not self.missing_input_files()

        stdin  = self._open_pipe(temp, self._stdin , "rb")
        stdout = self._open_pipe(temp, self._stdout, "wb")
        stderr = self._open_pipe(temp, self._stderr, "wb")

        self._temp = temp
        self._temp_files = self._generate_filenames(root = temp)
        
        kwords = dict(self._temp_files)
        kwords.update(self._in_files)

        cmd = [(field % kwords) for field in self._cmd]

        self.proc = subprocess.Popen(cmd, 
                                     stdin  = stdin,
                                     stdout = stdout,
                                     stderr = stderr)       


    def wait(self):
        """Equivalent to Popen.wait(), but returns the value wrapped in a list."""
        return [self.proc.wait()]


    def poll(self):
        """Equivalent to Popen.poll(), but returns the value wrapped in a list."""
        return [self.proc.poll()]


    def executable_exists(self):
        """Returns true if the executable for the command exists, false otherwise."""
        return fileutils.executable_exists(self._cmd[0])

    def missing_input_files(self):
        """Returns a list of input files that are required by the AtomicCmd,
        but which does not currently exist. This list should be empty, 
        otherwise the command is expected to fail."""
        return fileutils.missing_files(self._in_files.itervalues())
        

    def missing_output_files(self, ignore_pipes = False):
        """Checks that the expected output files have been generated. If
        'ignore_pipes' is True, files generated by stdout or stderr
        are ignored."""
        files = self._generate_filenames(root = self._dest, ignore_pipes = ignore_pipes)
        return fileutils.missing_files(files.itervalues())


    def missing_temp_files(self):
        return fileutils.missing_files(self._temp_files.itervalues())


    def commit(self):
        assert (self.poll() is not None)
        assert not self.missing_temp_files()

        # Close any implictly opened pipes
        for (_, handle) in self._handles.itervalues():
            handle.close()

        for key in self._temp_files:
            os.rename(self._temp_files[key], self._final_files[key])

        self.proc = None


    def __str__(self):
        temp = self._temp or "${TEMP}"
        kwords = self._generate_filenames(root = temp)
        kwords.update(self._in_files)
        
        def describe_pipe(pipe, prefix):
            if type(pipe) is str:
                return "%s '%s'" % (prefix, pipe)
            elif isinstance(pipe, AtomicCmd):
                return "%s [AtomicCmd]" % (prefix)
            elif pipe == AtomicCmd.PIPE:
                return "%s [PIPE]" % prefix
            else:
                return ""

        if self._stdout != self._stderr:
            out  = describe_pipe(self._stdout, " >")
            out += describe_pipe(self._stderr, " 2>")
        else:
            out  = describe_pipe(self._stdout, " &>")
        

        command = [(field % kwords) for field in self._cmd]
        return "<'%s'%s%s" % (" ".join(command), describe_pipe(self._stdin, " <"), out)


    def _group_io_by_prefix(self, io_kwords):
        for (key, value) in io_kwords.iteritems():
            if type(value) not in (str, unicode):
                raise RuntimeError("Invalid input file '%s' for '%s' is not a string: %s" \
                                     % (key, self.__class__.__name__, value))

            if key.startswith("IN_"):
                self._in_files[key] = value
            elif key.startswith("OUT_"):
                self._out_files[key] = value
            else:
                raise CmdError("Command contains unclassified argument: '%s' -> '%s'" \
                                   % (self.__class__.__name__, key))
        

    def _open_pipe(self, root, pipe, mode):
        if isinstance(pipe, AtomicCmd):
            assert mode == "rb"
            return pipe.proc.stdout
        elif not (type(pipe) is str):
            return pipe
        elif pipe not in self._handles:
            self._handles[pipe] = (mode, open(os.path.join(root, pipe), mode))

        pipe_mode, pipe = self._handles[pipe]
        if pipe_mode != mode:
            raise CmdError("Attempting to open pipe with different modes: '%s' -> '%s'" \
                               % (self, pipe))

        return pipe

    def _generate_filenames(self, root, ignore_pipes = False):
        filenames = {}
        for (key, filename) in self._out_files.iteritems():
            if not (key.startswith("PIPE_") and ignore_pipes):
                filenames[key] = os.path.join(root, filename)
        return filenames




class _CommandSet:
    def __init__(self, commands):
        self._commands = list(commands)

    def wait(self):
        return_codes = []
        for command in self._commands:
            return_codes.extend(command.wait())
        return return_codes

    def poll(self):
        return_codes = []
        for command in self._commands:
            return_codes.extend(command.poll())
        return return_codes

    def executable_exists(self):
        for command in self._commands:
            if not command.executable_exists():
                return False
        return True

    def missing_input_files(self):
        missing_files = []
        for command in self._commands:
            missing_files.extend(command.missing_input_files())
        return missing_files

    def missing_output_files(self, ignore_pipes = False):
        missing_files = []
        for command in self._commands:
            missing_files.extend(command.missing_output_files(ignore_pipes))
        return missing_files

    def missing_temp_files(self):
        missing_files = []
        for command in self._commands:
            missing_files.extend(command.missing_temp_files())
        return missing_files

    def commit(self):
        for command in self._commands:
            command.commit()

    def __str__(self):
        return "[%s]" % ", ".join(str(command) for command in self._commands)




class ParallelCmds(_CommandSet):
    """This class wraps a set of AtomicCmds, running them in parallel.
    This corresponds to a set of piped commands, which only terminate
    when all parts of the pipe have terminated. For example:
    $ dmesg | grep -i segfault | gzip > log.txt.gz

    Note that only AtomicCmds and ParallelCmds are allowed as 
    sub-commands for this class, since the model requires non-
    blocking commands."""

    def __init__(self, commands):
        _CommandSet.__init__(self, commands)

        for command in self._commands:
            if not isinstance(command, (AtomicCmd, ParallelCmds)):
                raise CmdError("ParallelCmds must only contain AtomicCmds or other ParallelCmds!")

    def run(self, temp):
        for command in self._commands:
            command.run(temp)




class SequentialCmds(_CommandSet):
    """This class wraps a set of AtomicCmds, running them sequentially.
    This class therefore corresponds a set of lines in a bash script, 
    each of which invokes a foreground task. For example:
    $ bcftools view snps.bcf | bgzip > snps.vcf.bgz
    $ tabix snps.vcf.bgz

    The list of commands may include any type of command. Note that
    the run function only returns once each sub-command has completed."""

    def __init__(self, commands):
        _CommandSet.__init__(self, commands)

        for command in self._commands:
            if not isinstance(command, (AtomicCmd, ParallelCmds)):
                raise CmdError("ParallelCmds must only contain AtomicCmds or other ParallelCmds!")


    def run(self, temp):
        for command in self._commands:
            command.run(temp)

            if any(command.wait()):
                break
