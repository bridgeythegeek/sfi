import concurrent.futures
import inspect
import os

class sfi:

    _WIN_DIRS = [
        'windows',
        'windows\\system32',
        'windows\\syswow64',
        'windows.old',
        'windows.old\\system32',
        'windows.old\\syswow64' 
    ]

    def __init__(self, items, max_workers=3, items_per_thread=1000, winexe=None):
        self.items = items
        self.max_workers = max_workers
        self.items_per_thread = items_per_thread
        if winexe is None:
            winexe = os.path.join(os.path.dirname(os.path.abspath(inspect.stack()[0].filename)), 'winexe.txt')
        self.winexes = {}
        with open(winexe) as f:
            winexes_ = [x.strip().lower() for x in f.readlines() if not x.startswith('#')]
        winexes_ = set(winexes_)
        temp = []
        for we in winexes_:
            temp.append(we)
            if 'windows' in we and not 'windows.old' in we:
                temp.append(we.replace('windows', 'windows.old'))
        winexes_ = temp
        del(temp) 
        for we in winexes_:
            parts = sfi.split_path(we)
            if parts[0] is None or parts[1] is None:
                raise Exception(f"Invalid format of Windows executable: {we}")
            if parts[0] in self.winexes:
                self.winexes[parts[0]].append(parts[1])
            else:
                self.winexes[parts[0]] = [parts[1]]

    @staticmethod
    def split_path(item):
        if '\\' in item:  # Windows
            parts = item.split('\\')
            return ['\\'.join(parts[1:-1]), parts[-1]]
        elif '/' in item: # *nix
            return item.rsplit('/', 1)
        else:
            return [None, None]

    def execute(self, i):
        result = []
        for item in self.items[i:i+self.items_per_thread]:
            matches = []
            # Check WinExe first.
            path, base = sfi.split_path(item)
            if path in sfi._WIN_DIRS:
                if not base in self.winexes[path]:
                    matches.append('NoWin')
            if len(matches) > 0:
                result.append((item, matches))
        return result

    def process(self):
        with concurrent.futures.ThreadPoolExecutor(self.max_workers) as executor:
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

    with open('test.txt') as f:
        todo = [x.strip().lower() for x in f.readlines() if not x.startswith('#')]
    print(sfi(todo, 2, 3).process())
    
