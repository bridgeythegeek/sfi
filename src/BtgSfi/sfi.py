import argparse
import concurrent.futures
import inspect
import logging
import openpyxl
import os
import tqdm

class sfi:

    _ENV = {
        '%systemdrive%':  None,
        '%windir%': 'windows'
    }

    _SWAP = {
        'windows.old': 'windows'
    }

    _win_dirs = []    

    def __init__(self, items, max_workers=3, items_per_thread=1000, winexe=None):
        self.items = items
        self.max_workers = max_workers
        self.items_per_thread = items_per_thread
        self.have_errors = False
        if winexe is None:
            winexe = os.path.join(os.path.dirname(os.path.abspath(inspect.stack()[0].filename)), 'winexe.txt')
        self.winexes = {}
        with open(winexe) as f:
            winexes_ = [x.strip().lower() for x in f.readlines() if not x.startswith('#')]
        winexes_ = set(winexes_)
        logging.debug(f"Read {len(winexes_):,} known goods.")
        for we in winexes_:
            parts = sfi.split_path(we)
            if parts[0] is None or parts[1] is None:
                raise Exception(f"Invalid format of Windows executable: {we}")
            if parts[0] in self.winexes:
                self.winexes[parts[0]].append(parts[1])
            else:
                self.winexes[parts[0]] = [parts[1]]

    @staticmethod
    def split_path(item, resolve=False):
        
        # Windows
        if '\\' in item:
            if not resolve:
                return item.rsplit('\\', 1)
            
            parts = item.split('\\')
            
            # Check for UNC
            if len(parts[0]) < 1: # UNC
                parts = parts[3:] # Chop the \\server\share
            
            # Check for environment variable
            elif parts[0][0] == '%' and parts[0][-1] == '%' and not parts[0] in sfi._ENV:
                raise Exception(f"Unhandled environment variable {parts[0]!r} in {item!r}")
            if parts[0] in sfi._ENV and not sfi._ENV[parts[0]] is None:
                parts.insert(1, sfi._ENV[parts[0]])
            
            # Check for fixups
            if parts[1] in sfi._SWAP:
                parts[1] = sfi._SWAP[parts[1]]
            
            return ['\\'.join(parts[1:-1]), parts[-1]]  # Drop the [A-Z]:
        
        # *nix
        elif '/' in item:
            if not resolve:
                return item.rsplit('/', 1)
        
        # Err??
        else:
            return [None, None]

    def execute(self, i):
        result = []
        for item in self.items[i:i+self.items_per_thread]:
            try:
                matches = []

                # Check WinExe first.
                path, base = sfi.split_path(item, resolve=True)
                if path in self.winexes:
                    if not base in self.winexes[path]:
                        matches.append('NoWin')
                if len(matches) > 0:
                    result.append((item, matches))

                # Then the rules
                # TODO

            except Exception as ex:
                logging.debug(f"Error whilst processing {item!r}: {ex}")
                self.have_errors = True
            
            self.pbar.update(1)
        return result

    def process(self):
        with concurrent.futures.ThreadPoolExecutor(self.max_workers) as executor:
            self.pbar = tqdm.tqdm(total=len(self.items))
            futures = []
            start = 0
            while start < len(self.items):
                futures.append(executor.submit(self.execute, start))
                start += self.items_per_thread
        result = []
        for future in concurrent.futures.as_completed(futures):
            result.extend(future.result())
        return result   


if __name__ == '__main__':

    argp = argparse.ArgumentParser()
    argp.add_argument('file')
    args = argp.parse_args()

    logging.getLogger().setLevel(logging.DEBUG)

    with open(args.file, encoding='utf-8') as f:
        todo = [x.strip().lower() for x in f.readlines() if not x.startswith('#')]
    for match in sfi(todo, 2, 3).process():
        print(f"{match[0]}: {', '.join(match[1])}")
