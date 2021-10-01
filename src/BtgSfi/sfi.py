import argparse
import concurrent.futures
import inspect
import json
import logging
import os
import re
import tqdm

class sfi:

    _ENV = {
        r'%systemdrive%':  None,
        r'%hot%': 'c:',
        r'%windir%': 'windows',
        r'%osdrive%': 'c:',
        r'%system32%': 'windows\system32',
        r'%programfiles%': 'program files'
    }

    _SWAP = {
        'windows.old': 'windows'
    }

    def __init__(self, items, max_workers=3, items_per_thread=1000, winexe_file=None, rules_files=None):
        
        self.items = items
        self.max_workers = max_workers
        self.items_per_thread = items_per_thread
        self.have_errors = False

        # Read rules file
        self.rules = []
        if not rules_files is None:
            for rules_file in rules_files:
                new_rules = self.validate_rules(rules_file)
                if new_rules:
                    self.rules.extend(new_rules)
        logging.info(f"Got {len(self.rules)} rules.")
        
        # Read winexe file
        if winexe_file is None:
            winexe_file = os.path.join(os.path.dirname(os.path.abspath(inspect.stack()[0].filename)), 'winexe.txt')
        self.winexes = {}
        with open(winexe_file) as f:
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
    def split_path(item, resolve=True):
        
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
            return item.rsplit('/', 1)
        
        # Assume it's just a file name, no path
        else:
            return ['', item]

    def validate_rules(self, rules_file):
        logging.info(f"Validating rules file {rules_file!r}.")
        
        j = None
        with open(rules_file) as f:
            rules = json.load(f)
        
        if not rules:
            return None
        
        new_rules = []
        all_valid = True
        for rule_i, rule in enumerate(rules):
            if 'enabled' in rule and rule['enabled'] == False:
                logging.info(f"Rule {rule_i} is disabled.")
                continue

            valid = True
            
            if not all([x in rule.keys() for x in ('name', 'conditions')]):
                logging.error(f"Rule {rule_i}, must contain both 'name' and 'conditions'.")
                valid = all_valid = False
                
            for key in rule.keys():
                if not key in ('name', 'enabled', 'comment', 'conditions', 'and'):
                    logging.error(f"Invalid key in rule {rule_i}: {key}")
                    valid = False
                if key in ('name', 'comment') and not isinstance(rule[key], str):
                    logging.error(f"In rule {rule_i}, key {key} must be a string.")
                    valid = False
                if key in ('enabled'):
                    logging.debug(type(rule[key]))
                if key in ('conditions'):
                    if isinstance(rule[key], list):
                        for condition_i, condition in enumerate(rule[key]):
                            for condition_key in condition.keys():
                                if condition_key == 'element':
                                    pass
                                elif condition_key == 'criteria':
                                    if not condition[condition_key] in ('is', 'starts', 'ends', 'contains', 'regex'):
                                        logging.error(f"In rule {rule_i}, condition {condition_i}, invalid criteria: {condition[condition_key]}")
                                        valid = False
                                elif condition_key == 'value':
                                    pass
                                elif condition_key == 'case':
                                    pass
                    else:
                        logging.error(f"In rule {rule_i}, key {key} must be a list.")
                        valid = False
                        continue

            if valid:
                new_rules.append(rule)
            else:
                all_valid = False
        
        if not all_valid:
            return None
        
        return new_rules
    
    @staticmethod
    def check_rule(rule, item, path_, base):
        logging.debug(f"{rule['name']=}, {item=}, {path_=}, {base=}")

        is_or = True
        if 'and' in rule and rule['and'] == True:
            is_or = False

        matched = False
        for condition in rule['conditions']:
            
            values = condition['value']
            if isinstance(values, str):
                values = [values]
            
            case = False
            if 'case' in condition and condition['case']:
                case = True
            if not case:
                item = item.lower()
                path_ = path_.lower()
                base = base.lower()
                values = [v.lower() for v in values]
            
            negate = False
            if 'negate' in condition and condition['negate']:
                negate = True
            
            element = item
            if condition['element'] == 'path':
                element = path_
            elif condition['element'] == 'base':
                element = base
            
            if condition['criteria'] == 'contains':
                matched = any([v in element for v in values])
            elif condition['criteria'] == 'is':
                matched = any([v == element for v in values])
            elif condition['criteria'] == 'starts':
                matched = any([element.startswith(v) for v in values])
            elif condition['criteria'] == 'ends':
                matched = any([element.endswith(v) for v in values])
            elif condition['criteria'] == 'regex':
                matched = any([re.search(v, element) for v in values])
            else:
                logging.error(f"In rule {rule['name']!r}, unknown criteria: {condition['criteria']}")
            
            if negate:
                matched = not matched
            if matched and is_or:
                break
            if not matched and not is_or:
                break

        return matched

    def execute(self, i, do_winexe=True):
        result = []
        for item in self.items[i:i+self.items_per_thread]:
            try:
                matches = []

                logging.debug(item)
                path_, base = sfi.split_path(item)
                logging.debug(f"{path_}, {base}")

                # Check WinExe first.
                if do_winexe:
                    if path_ in self.winexes:
                        if not base in self.winexes[path_]:
                            matches.append('NoWin')
                
                # Then the rules
                for rule in self.rules:
                    if sfi.check_rule(rule, item, path_, base):
                        matches.append(rule['name'])
                
                if len(matches) > 0:
                    result.append((item, matches))

            except Exception as ex:
                logging.error(f"Error whilst processing {item!r}: {ex}")
                self.have_errors = True
            
            self.pbar.update(1)
        return result

    def process(self, do_winexe=True):
        if len(self.rules) < 1 and not do_winexe:
            logging.info("No winexe and no rules. Nothing to do.")
            return []

        with concurrent.futures.ThreadPoolExecutor(self.max_workers) as executor:
            self.pbar = tqdm.tqdm(total=len(self.items))
            futures = []
            start = 0
            while start < len(self.items):
                futures.append(executor.submit(self.execute, start, do_winexe))
                start += self.items_per_thread
        result = []
        for future in concurrent.futures.as_completed(futures):
            if future.exception():
                logging.error(future.exception())
                continue
            result.extend(future.result())
        self.pbar.close()
        return result   


if __name__ == '__main__':

    argp = argparse.ArgumentParser()
    argp.add_argument('--file', '-f', metavar='files.txt', help="Text file of file paths to check", required=True)
    argp.add_argument('--winexe', metavar="winexe.txt", help="Text file of known good Windows exes", default="winexe.txt")
    argp.add_argument('--rules', metavar="rules.json", help="Rules to detect evil", nargs="+")
    argp.add_argument('--debug', action="store_true", help="Debug level")
    args = argp.parse_args()

    logging.getLogger().setLevel(logging.DEBUG if args.debug else logging.INFO)
    
    do_winexe = args.winexe.lower()!='nil'
    logging.debug(f"{do_winexe=}")

    with open(args.file, encoding='utf-8') as f:
        todo = [x.strip().lower() for x in f.readlines() if not x.startswith('#')]
    logging.debug(f"Read {len(todo):,} items from {args.file!r}.")

    for match in sfi(todo, max_workers=1, items_per_thread=3, rules_files=args.rules).process(do_winexe):
        print(f"{match[0]}: {', '.join(match[1])}")
