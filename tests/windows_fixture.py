import subprocess
import os

def junction(link, target):
    if os.name != 'nt':
        link.symlink_to(target, target_is_directory=True)
        return
    subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                   check=True, capture_output=True)
