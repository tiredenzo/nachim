import subprocess


def command(args, *, last_error_line=False, stdout_only=False, timeout=None):
    stderr = subprocess.PIPE if stdout_only else subprocess.STDOUT
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=stderr, timeout=timeout,
                                text=True, errors="replace", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{args[0]} timed out. Check the phone connection and retry.") from error
    output = result.stdout.strip()
    if result.returncode:
        if result.stderr:
            output = (output + "\n" + result.stderr).strip()
        if last_error_line:
            raise RuntimeError(output.splitlines()[-1] if output else f"{args[0]} failed.")
        raise RuntimeError(output or f"{args[0]} exited with status {result.returncode}")
    return output
