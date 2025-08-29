#!/usr/bin/env python
"""A simple cmd2 application."""
import functools
import glob
import os
import urllib.request
import importlib
import importlib.metadata
from copy import copy
from io import StringIO
from typing import Any, Optional, Union, List

import cmd2
import ruamel.yaml
from git.exc import InvalidGitRepositoryError, GitCommandError
from pydantic.fields import FieldInfo
from pydantic import ValidationError
from rich.console import Console

from settingsrepo import Settingsrepo

try:
    from settings_fields import f_root
except ImportError:
    f_root = None


yaml = ruamel.yaml.YAML()
yaml.indent(sequence=4, offset=2)
yaml.default_flow_style = False
yaml.preserve_quotes = True
yaml.width = 1000

console = Console()


def get_pydantic_type(token_path: list[str]) -> Union[FieldInfo, str, None]:
    current_field = f_root
    next_list_or_dict = None
    prev_type = None
    for token in token_path:
        if next_list_or_dict:
            current_field = current_field.model_fields[next_list_or_dict]
            next_list_or_dict = None
            continue
        if hasattr(current_field, "model_fields"):
            current_field = current_field.model_fields[token]
            annotation = current_field.annotation
            if annotation._name == 'Optional':
                current_field = current_field.annotation.__args__[0]
                next_list_or_dict = token
        elif hasattr(current_field, "annotation"):
            annotation = current_field.annotation
            if annotation._name == 'Optional':
                current_field = current_field.annotation.__args__[0]
                next_list_or_dict = token

            if annotation._name == "List":
                prev_type = list
                current_field = annotation.__args__[0]
            if annotation._name == "Dict":
                current_field = annotation.__args__[1]
        else:
            break
    #print(current_field)
    if hasattr(current_field, "model_fields"):
        return current_field
    elif hasattr(current_field, "annotation"):
        if is_list_of_dicts_field(current_field):
            return "List of dicts"
        return current_field.annotation._name
    elif hasattr(current_field, "_name"):
        return current_field._name
    else:
        return None


def convert_list_of_dicts(obj: Any, find_key: str):
    compatible = True
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                if find_key not in item.keys():
                    compatible = False
                    break
            else:
                compatible = False
                break
    else:
        compatible = False
    if compatible:
        result = {}
        for interface in obj:
            result[interface[find_key]] = interface.copy()
        return result
    else:
        return obj


def is_list_of_dicts_field(obj):
    if hasattr(obj, 'annotation') and obj.annotation._name == 'List' and hasattr(obj.annotation.__args__[0], 'model_fields'):
        return True
    return False


def find_dict_by_key(obj, find_key, name, setter=False):
    for i, interface in enumerate(obj):
        if interface[find_key] == name:
            return i
    if setter:
        obj.append({find_key: name})
        return len(obj) - 1
    return None


class CnaasYamlCliApp(cmd2.Cmd):
    """A simple cmd2 application."""
    complete_cd = functools.partialmethod(cmd2.Cmd.path_complete, path_filter=os.path.isdir)

    def __init__(self):
        super().__init__()
        self.validate_repo()
        self._set_prompt()
        self.register_precmd_hook(self.color_prompt_input)
        self.debug = True
        remove_bulitins = ["do_run_pyscript", "do_run_script", "do_shortcuts", "do_set"]
        setattr(self, "do_cli_set", super(CnaasYamlCliApp, self).do_set)
        for cmd in remove_bulitins:
            delattr(cmd2.Cmd, cmd)
        if f_root is None and self.valid_repo:
            self.get_datamodel(None)

    def color_prompt_input(self, data: cmd2.plugin.PrecommandData) -> cmd2.plugin.PrecommandData:
        print(data.statement)
        return data

    def _set_prompt(self) -> None:
        """Set prompt so it displays the current working directory."""
        self.cwd = os.getcwd()
        color = cmd2.ansi.Fg.RED
        if self.valid_repo:
            if self.repo.repo.is_dirty():
                color = cmd2.ansi.Fg.LIGHT_CYAN
            else:
                color = cmd2.ansi.Fg.GREEN
            path = os.path.split(self.cwd)[-1]
        else:
            path = self.cwd
        self.prompt = cmd2.ansi.style(path, fg=color, bold=True) + ' > '

    def postcmd(self, stop: bool, _line: str) -> bool:
        """Hook method executed just after a command dispatch is finished.

        :param stop: if True, the command has indicated the application should exit
        :param line: the command line text for this command
        :return: if this is True, the application will exit after this command and the postloop() will run
        """
        """Override this so prompt always displays cwd."""
        self._set_prompt()
        return stop

    def complete_last_token(self, current_field, last_token):
        if current_field == bool:
            return [cur_match for cur_match in ['true', 'false'] if cur_match.startswith(last_token)]
        if current_field == str:
            return []
        if current_field == int:
            return []
        if hasattr(current_field, "model_fields"):
            return [cur_match for cur_match in current_field.model_fields.keys() if cur_match.startswith(last_token)]
        else:
            return []

    def complete_ifclass(self, tokens, index):
        ifclasses = ['custom', 'downlink', 'fabric', 'mirror']
        try:
            with open(os.path.join(self.cwd, "global", "base_system.yml")) as f:
                base_system_data = yaml.load(f)
                if "port_template_options" in base_system_data:
                    ifclasses.extend([f"port_template_{name}" for name in base_system_data["port_template_options"].keys()])
        except Exception:
            pass
        return [cur_match for cur_match in ifclasses if cur_match.startswith(tokens[index])]

    def get_list_of_dict_primary_key(self, tokens, token):
        list_of_dicts_keys = [
            {"path": ["interfaces"], "primary_key": "name"},
            {"path": ["extroute_bgp", "vrfs"], "primary_key": "name"},
            {"path": ["extroute_static", "vrfs"], "primary_key": "name"},
            {"path": ["extroute_bgp", "vrfs", "*", "neighbor_v4"], "primary_key": "peer_ipv4"},
            {"path": ["extroute_bgp", "vrfs", "*", "neighbor_v6"], "primary_key": "peer_ipv6"},
            {"path": ["ntp_servers"], "primary_key": "host"},
            {"path": ["syslog_servers"], "primary_key": "host"},
            {"path": ["radius_servers"], "primary_key": "host"},
            {"path": ["vrfs"], "primary_key": "name"},
        ]
        path_start_index = 2
        for item in list_of_dicts_keys:
            for i in range(0, len(tokens)-path_start_index):
                if item["path"][i] == "*":
                    continue
                if not isinstance(tokens[i+path_start_index], str):
                    break
                if item["path"][i] == tokens[i+path_start_index] and i == len(item["path"])-1:
                    if tokens[i+path_start_index] == token:
                        yield item["primary_key"]
                        continue
                    break
                if item["path"][i] != tokens[i+path_start_index]:
                    break

    def get_next_yaml_item(self, tokens, token, yaml_item, convert=True) -> (Any, Optional[str]):
        primary_key = None
        for item in self.get_list_of_dict_primary_key(tokens, token):
            primary_key = item
            if convert:
                return convert_list_of_dicts(yaml_item[token], primary_key), primary_key
            break

        try:
            if isinstance(yaml_item, list):
                if token is None:
                    raise ValueError("List element not found")
                if token < len(yaml_item):
                    return yaml_item[token], primary_key
            elif isinstance(yaml_item, dict) and token in yaml_item:
                return yaml_item[token], primary_key
            return None, None
        except Exception as e:
            raise e
            if isinstance(yaml_item, dict) and token in yaml_item:
                return yaml_item[token], primary_key

    def color_paths(self, completions: list[str]) -> list[str]:
        #if len(completions) == 1:
        #    return [cmd2.ansi.style(f"{completions[0]}", fg=cmd2.Fg.GREEN)]
        return completions


    def settings_complete(
            self,
            text: str,
            line: str,
            begidx: int,  # noqa: ARG002
            endidx: int,
            suggest_set: bool = False
    ):
        """Complete yaml files"""

        tokens, _ = self.tokens_for_completion(line, begidx, endidx)
        if not tokens:  # pragma: no cover
            return []

        index = len(tokens) - 1

        if index == 1:
            return self.color_paths(self.path_complete(text, line, begidx, endidx))

        try:
            # first token is the command, second is the file name
            filename = os.path.join(os.getcwd(), tokens[1])
            with open(filename) as f:
                yaml_item = yaml.load(f.read())
                token_path = []
                # if there are more tokens, we need to dig into the yaml
                # structure to find the right list or dict to complete
                for dict_level in range(2, len(tokens)-1):
                    token = tokens[dict_level]
                    if token.isdigit():
                        token = int(token)
                    #TODO:
                    # check if yaml_item[token] is a list of dictionaries where all dictionaries have the key name
                    token_path.append(token)
                    yaml_item, _ = self.get_next_yaml_item(tokens, token, yaml_item)
                current_token = tokens[len(tokens)-1]

                # if suggest set, walk according to the model fields
                if suggest_set:
                    if len(token_path) == 0:
                        return [cur_match for cur_match in f_root.model_fields.keys() if cur_match.startswith(tokens[index])]
                    current_field = f_root
                    inspect_inner = False
                    inspect_inner_list = False
                    suggest_on_empty_yaml = None
                    for idx, token in enumerate(token_path):
                        is_last_token = idx == len(token_path) - 1
                        #print(f"DEBUG idx: {idx}, token: {token}, {len(token_path)}, last = {is_last_token}, yaml_item: {str(yaml_item)[:20]}")
                        if idx == 0 and token not in f_root.model_fields:
                            print(f"\nUnknown setting: {token}")
                            cmd2.cmd2.rl_force_redisplay()
                            return []
                        elif idx == 0:
                            current_field = f_root.model_fields[token]
                            if current_field.annotation._name == 'Optional':
                                current_field = current_field.annotation.__args__[0]
                            continue
                        # idx > 0
                        if inspect_inner:
                            current_field = current_field.model_fields[token]
                            inspect_inner = False

                        if inspect_inner_list:
                            current_field = current_field.annotation.__args__[0]
                            inspect_inner_list = False

                        if is_last_token and (hasattr(current_field, 'model_fields') or hasattr(current_field, "annotation") ):
                            current_subfield = None
                            if hasattr(current_field, "annotation") and current_field.annotation in [int, bool, str]:
                                current_subfield = current_field
                            elif hasattr(current_field, "model_fields") and token in current_field.model_fields:
                                current_subfield = current_field.model_fields[token]
                            if current_subfield:
                                current_subfield_type = None
                                if hasattr(current_subfield, "annotation") and hasattr(current_subfield.annotation, "_name") and current_subfield.annotation._name == "Optional":
                                    current_subfield_type = current_subfield.annotation.__args__[0]
                                if current_subfield_type == bool or current_subfield.annotation == bool:
                                    print(f"\n{token}: {current_subfield.description}")
                                    cmd2.cmd2.rl_force_redisplay()
                                    return [cur_match for cur_match in ['true', 'false'] if
                                            cur_match.startswith(tokens[index])]
                                elif current_subfield_type == str or current_subfield.annotation == str:
                                    print(f"\n{token}: {current_subfield.description}")
                                    cmd2.cmd2.rl_force_redisplay()
                                    if is_last_token and token_path[0] == 'interfaces' and token_path[2] == 'ifclass':
                                        return self.complete_ifclass(tokens, index)
                                    else:
                                        return []
                                elif current_subfield_type == int or current_subfield.annotation == int:
                                    print(f"\n{token}: {current_subfield.description}")
                                    cmd2.cmd2.rl_force_redisplay()
                                    return []
                        if not hasattr(current_field, 'annotation'):
                            # try to complete from yaml data instead of model
                            if hasattr(current_field, 'model_fields'):
                                #current_field = current_field.model_fields[token]
                                pass
                            else:
                                inspect_inner = True
                            if list_of_dicts and isinstance(token, int):
                                inspect_inner = True
                            if list_of_dicts and isinstance(token, int) and is_last_token:
                                return [cur_match for cur_match in current_field.model_fields.keys() if
                                        cur_match.startswith(tokens[index])]
                            continue
                        if hasattr(current_field.annotation, "_name") and current_field.annotation._name == 'Optional':
                            if is_last_token:
                                print(f"\n{token} (optional): {current_field.description}")
                                cmd2.cmd2.rl_force_redisplay()
                            current_field = current_field.annotation.__args__[0]
                            if hasattr(current_field, 'annotation') and current_field.annotation._name in ['Dict', 'List']:
                                inspect_inner = True
                            if is_last_token:
                                return self.complete_last_token(current_field, tokens[index])
                        if hasattr(current_field.annotation, "_name") and current_field.annotation._name == 'List':
                            # compare
                            primary_key = None
                            for item in self.get_list_of_dict_primary_key(tokens[:-2], tokens[-3]):
                                primary_key = item
                            list_of_dicts = is_list_of_dicts_field(current_field)
                            #if list_of_dicts == (primary_key is None):
                                #list_of_dicts = (primary_key is not None)
                            if list_of_dicts and primary_key is None:
                                print(f"\nDiff: {list_of_dicts} != {primary_key}, {token}, -> {(primary_key is not None)}")
                                cmd2.cmd2.rl_force_redisplay()
                                # should not be set on list, -> return []   but then should be set inside list of dict?
                                # if List then continue, otherwise set current_field
                                if hasattr(current_field, 'annotation') and current_field.annotation._name in ['List']:
                                    inspect_inner_list = True  # should this be set only if not is_last_token?
                                    continue
                                else:
                                    current_field = current_field.annotation.__args__[0] ## dbg1
                                #inspect_inner_list = True
                                # inspect inner list element, not inner field
                                #inspect_inner = True
                                pass
                            else:
                                current_field = current_field.annotation.__args__[0]
                            if is_last_token:
                                # for interfaces, we should complete interface options
                                if primary_key is None:
                                    ##
                                    suggest_on_empty_yaml = ['[]']  # if existing list elements, suggest creating empty list
                                    if not hasattr(current_field, "model_fields"):
                                        #pass  ## dbg1
                                        continue
                                return [cur_match for cur_match in current_field.model_fields.keys() if
                                        cur_match.startswith(tokens[index])]
                            else:
                                # do inspect interfaces, but not statements?? because interfaces is converted to dict?
                                if list_of_dicts:
                                    if not is_last_token:
                                        pass
                                        #inspect_inner = False
                        elif hasattr(current_field.annotation, "_name") and current_field.annotation._name == 'Dict':
                            current_field = current_field.annotation.__args__[1]
                            if is_last_token:
                                return self.complete_last_token(current_field, tokens[index])
                            else:
                                inspect_inner = True
                        else:
                            if is_last_token:
                                return [cur_match for cur_match in current_field.annotation.model_fields.keys() if cur_match.startswith(tokens[index])]
                            else:
                                current_field = current_field.model_fields[token]

                if isinstance(yaml_item, list):
                    list_items = [cur_match for cur_match in list(map(str, range(len(yaml_item)))) if cur_match.startswith(tokens[index])]
                    if not list_items:
                        return ['0']
                    else:
                        return list_items
                elif isinstance(yaml_item, dict):
                    available_options = set(yaml_item.keys())
                    return [cur_match for cur_match in available_options if cur_match.startswith(tokens[index])]
                else:
                    if suggest_set and suggest_on_empty_yaml != None:
                        return suggest_on_empty_yaml
                    else:
                        return []

        except Exception as e:
            console.log(type(e))
            print(str(e))
            if suggest_set:
                raise e
            else:
                print(yaml_item)

            return []


    def do_pull(self, args):
        """Perform git pull on setting repository"""
        self.repo.pull()

    def validate_repo(self, quiet=True):
        try:
            self.repo = Settingsrepo(os.getcwd())
            self.valid_repo = True
        except InvalidGitRepositoryError:
            if not quiet:
                console.log("Not a git repository")
            self.valid_repo = False


    def do_cd(self, args):
        """Change directory"""
        if not args:
            newdir = os.path.expanduser("~")
        else:
            newdir = args
        if not os.path.isdir(newdir):
            console.log(f"{newdir} is not a directory")
        else:
            os.chdir(newdir)
            console.log(f"Changed directory to {os.getcwd()}")
        self.validate_repo(quiet=False)

    def do_initdevice(self, statement: cmd2.Statement):
        """Initialize a device by creating a directory with empty yaml files in the devices/ directory"""
        if not self.valid_repo:
            console.log("Not a valid git repository")
            return
        if not statement.argv or len(statement.argv) != 2:
            console.log("Usage: initdevice <device_hostname>")
            return
        device_hostname = statement.argv[1]
        newpath = os.path.join(self.cwd, "devices", device_hostname)
        os.mkdir(newpath)
        os.mknod(os.path.join(newpath, "interfaces.yml"))
        os.mknod(os.path.join(newpath, "routing.yml"))
        os.mknod(os.path.join(newpath, "base_system.yml"))
        empty_int = {'interfaces': []}
        with open(os.path.join(newpath, "interfaces.yml"), "w") as f:
            yaml.dump(empty_int, f)
        self.repo.repo.index.add([os.path.join(newpath, "interfaces.yml")])
        self.repo.repo.index.add([os.path.join(newpath, "routing.yml")])
        self.repo.repo.index.add([os.path.join(newpath, "base_system.yml")])

    def do_commit(self, statement: cmd2.Statement):
        """Perform git commit -am, ask for commit message"""
        if not self.valid_repo:
            console.log("Not a valid git repository")
            return
        if not self.repo.repo.is_dirty():
            console.log("Working tree is clean, nothing to commit")
            return
        self.do_diff(statement)
        message = input("Enter commit message: ")
        with console.status("Committing changes"):
            ret = self.repo.repo.git.commit("-a", "-m", message)
            print(ret)

    def do_commit_and_push(self, statement: cmd2.Statement):
        """Perform git commit and then git push"""
        self.do_commit(statement)
        with console.status("Pushing changes"):
            self.repo.repo.git.push()

    def complete_find(self, text, line, begidx, endidx):
        tokens, _ = self.tokens_for_completion(line, begidx, endidx)

        return [f"{cur_match}:" for cur_match in f_root.model_fields.keys() if cur_match.startswith(tokens[1])]

    def do_find(self, statement: cmd2.Statement):
        """Perform a git grep on the repository"""
        if not self.valid_repo:
            console.log("Not a valid git repository")
            return
        if len(statement.argv) < 2:
            console.log("Usage: find <expression>")
            return
        expression = statement.argv[1]
        try:
            ret = self.repo.repo.git.grep(expression)
        except GitCommandError:
            console.log("No matches found")
            return
        for line in ret.split("\n"):
            import re
            matches = re.match(r"^(?P<file>[^:]+):(?P<match>.*):$", line)
            groups = matches.groups()
            print(groups)
            cmd2.Statement()
            self.do_show(cmd2.Statement(groups[0]))
        console.print(ret)

    def complete_show(self, text, line, begidx, endidx):
        return self.settings_complete(text, line, begidx, endidx)

    def yaml_get_helper(self, argv, yaml_text):
        yaml_item = yaml.load(yaml_text)

        next_find_dict_key = None
        for dict_level in range(2, len(argv)):
            token = argv[dict_level]

            if token.isdigit():
                token = int(token)

            if next_find_dict_key is not None:
                token = find_dict_by_key(yaml_item, next_find_dict_key, token)
                next_find_dict_key = None

            yaml_item, primary_key = self.get_next_yaml_item(argv, token, yaml_item, convert=False)
            if primary_key:
                next_find_dict_key = primary_key
            #if isinstance(yaml_item, list) and token < len(yaml_item):
            #    yaml_item = yaml_item[token]
            #elif token in yaml_item:
            #    yaml_item = yaml_item[token]
            #if is_list_of_dicts(yaml_item, "name"):
            #    next_find_dict_key = "name"
        return yaml_item

    def yaml_set_helper(self, argv, yaml_item, set_value=None):
        token_path = []

        next_find_dict_key = None
        next_append_list = False
        new_key = False
        new_parent_key = False
        new_element = False
        new_parent_element = False
        final_set_value = set_value
        for dict_level in range(2, len(argv)):
            next_append_list = False
            token = argv[dict_level]

            if token.isdigit():
                token = int(token)

            if next_find_dict_key is not None:
                token = find_dict_by_key(yaml_item, next_find_dict_key, token, setter=True)
                next_find_dict_key = None

            token_path.append(token)
            try:
                yaml_item[token]
            except (KeyError, TypeError):
                if dict_level == len(argv) - 1:
                    new_key = True
                elif dict_level == len(argv) - 2:
                    new_parent_key = True
            except IndexError:
                if dict_level == len(argv) - 1:
                    new_element = True
                elif dict_level == len(argv) - 2:
                    new_parent_element = True
            else:
                # don't update yaml_item for last token, since we want to update mutable object dict/list instead of immutable object str/int/bool
                if dict_level != len(argv) - 1:
                    yaml_item, next_find_dict_key = self.get_next_yaml_item(argv, token, yaml_item, convert=False)
                    #yaml_item = yaml_item[token]

            #if is_list_of_dicts(yaml_item, "name"): -> set elif
            #    next_find_dict_key = "name"
            # if last element in yaml is a list, or if pydantic model thinks it should become a list
            # TODO: generic way
            #was elif
            try:
                key_type = get_pydantic_type(token_path)
            except Exception as e:
                key_type = None
            if key_type == "List":
                #print("Appending to list")
                next_append_list = True
                # if list already exists, update yaml_item so old_value will be old list
                if not new_key and isinstance(yaml_item[token], list):
                    yaml_item = yaml_item[token]

        if set_value.isdigit():
            set_value = int(set_value)
        elif set_value == '[]':
            set_value = []
        if new_key:
            old_value = None
        elif next_append_list:
            old_value = copy(yaml_item)
        else:
            old_value = copy(yaml_item[argv[-1]])
        if key_type == "List of dicts":
            # if old_value is list, and set value is not list?
            if isinstance(old_value, List) and not isinstance(set_value, List):
                # new empty key, set primary_key to set_value
                primary_key = None
                for item in self.get_list_of_dict_primary_key(argv, token):
                    primary_key = item
                if primary_key:
                    new_list = copy(old_value)
                    new_list.append({primary_key: set_value})
                    set_value = new_list
                    final_set_value = new_list

        if next_append_list:
            if new_key:
                yaml_item[argv[-1]] = [set_value]
                final_set_value = yaml_item[argv[-1]]
            elif isinstance(yaml_item, list):
                yaml_item.append(set_value)
                final_set_value = yaml_item
        else:
            if new_parent_key:
                yaml_item[token_path[-2]] = {}
                yaml_item = yaml_item[token_path[-2]]
            elif new_parent_element:
                yaml_item.append({})
                yaml_item = yaml_item[-1]
            if token == "config":
                yaml_item[argv[-1]] = ruamel.yaml.scalarstring.LiteralScalarString(set_value)
            else:
                yaml_item[argv[-1]] = set_value
        token_path.append(argv[-1])
        return token_path, old_value, final_set_value

    def complete_set(self, text, line, begidx, endidx):
        return self.settings_complete(text, line, begidx, endidx, suggest_set=True)

    def do_show(self, statement: cmd2.Statement) -> None:
        """Usage: show [filepath] [yaml_key1] [yaml_key2] ...

        Shows yaml setting specified by filepath and yaml keys. Filepath can use globbing.
        """
        for filename in glob.glob(os.path.join(os.getcwd(), statement.argv[1])):
            if not os.path.isfile(filename):
                console.log(f"{filename} is not a file")
                continue
            with open(filename) as f:
                text = f.read()
                short_filename = filename.removeprefix(os.getcwd()+os.path.sep)
                console.log(f"{short_filename}:")
                if len(statement.argv) == 2:
                    with console.pager():
                        console.print(text)
                elif len(statement.argv) >= 3:
                    try:
                        yaml_item = self.yaml_get_helper(statement.argv, text)
                    except ValueError as e:
                        console.log(f"{e} ({' -> '.join(statement.argv[2:])})")
                        continue

                    string_stream = StringIO()
                    yaml.dump(yaml_item, string_stream)
                    yaml_str = string_stream.getvalue()
                    if yaml_str.count('\n') > console.height:
                        with console.pager():
                            console.print(yaml_str)
                    else:
                        console.print(yaml_str)

    def do_set(self, statement: cmd2.Statement) -> None:
        """Usage: set [filepath] [yaml_key1] [yaml_key2] ... [value]

        Sets yaml setting specified by filepath and yaml keys to value. Filepath can use globbing.
        """
        for filename in glob.glob(os.path.join(os.getcwd(), statement.argv[1])):
            if not os.path.isfile(filename):
                console.log(f"{filename} is not a file")
                continue
            with open(filename) as f:
                text = f.read()
                console.log(f"{filename}:")
                if len(statement.argv) == 2:
                    console.log("You must specify a setting to change")
                elif len(statement.argv) >= 3:
                    #yaml_item = self.yaml_get_helper(statement.argv[:-1], text)

                    #string_stream = StringIO()
                    #yaml.dump(yaml_item, string_stream)
                    #yaml_str = string_stream.getvalue().removesuffix("\n...\n").strip()

                    set_value = statement.argv[-1]
                    #if yaml_str == set_value:
                    #    print("same")
                    #    continue
                    yaml_item = yaml.load(text)
                    pre_num_errors = 0
                    try:
                        f_root(**yaml_item).model_dump()
                    except ValidationError as e:
                        pre_num_errors = len(e.errors())

                    token_path, old_value, final_set_value = self.yaml_set_helper(statement.argv[:-1], yaml_item, set_value)
                    try:
                        f_root(**yaml_item).model_dump()
                    except ValidationError as e:
                        console.log(f"Error: {e}")
                        console.print("Repository syntax reference:")
                        console.print("https://cnaas-nms.readthedocs.io/en/latest/reporef/index.html")
                        post_num_errors = len(e.errors())
                        if pre_num_errors < post_num_errors:
                            continue_anyway = input("Syntax did not validate. Set value anyway? [y/N] ")
                            if continue_anyway.lower() != "y":
                                continue
                    if old_value == final_set_value:
                        console.log("Value unchanged")
                    else:
                        console.log(f"{' -> '.join([str(x) for x in token_path])} was updated: {old_value} -> [bold red]{final_set_value}[/bold red]")

                    with open(filename, "wb") as f:
                        yaml.dump(yaml_item, f)

    def do_diff(self, statement: cmd2.Statement) -> None:
        """Perform a git diff on the repository"""
        print(self.repo.repo.git.diff("--color"))

    def do_reset(self, statement: cmd2.Statement) -> None:
        """Perform git reset --hard, removing all local changes that has not been comitted"""
        self.do_diff(statement)
        confirm_reset = input("Are you sure you want to reset and permanently destroy all local changes? [y/N] ")
        if confirm_reset.lower() != "y":
            return
        self.repo.repo.git.reset("--hard")

    def do_get_datamodel(self, statement: Optional[cmd2.Statement]) -> None:
        """Download a datamodel file from github so tab completion has something to work with"""
        self.get_datamodel(statement)

    def get_datamodel(self, statement: Optional[cmd2.Statement]) -> None:
        datamodel_file = os.path.join(os.path.split(__file__)[0], "settings_fields.py")
        if os.path.isfile(datamodel_file):
            confirm_delete = input("Datamodel file exists, remove it? [y/N] ")
            if confirm_delete.lower() == "y":
                os.remove(datamodel_file)
            else:
                return
        while True:
            get_version = input("Which version of the datamodel to get? (press enter for default: heads/develop, or type tags/v1.6.0 ex) ")
            if get_version == "":
                get_version = "heads/develop"
            url = f"https://raw.githubusercontent.com/SUNET/cnaas-nms/refs/{get_version}/src/cnaas_nms/db/settings_fields.py"
            # download file from url and save to filename datamodel_file
            try:
                with console.status("Downloading datamodel"):
                    urllib.request.urlretrieve(url, datamodel_file)
            except urllib.error.HTTPError as e:
                print(f"Error downloading datamodel file: {e}")
                continue
            print(f"Datamodel file saved to {datamodel_file}")
            global f_root
            f_root = importlib.import_module("settings_fields").f_root
            break

    def do_version(self, statement: cmd2.Statement) -> None:
        """Show version"""
        try:
            print(f"cnaas-yaml-cli version {importlib.metadata.version('cnaas_yaml_cli')}")
        except Exception:
            print("cnaas-yaml-cli version unknown, package not installed via pip")




if __name__ == '__main__':
    import sys

    setattr(CnaasYamlCliApp, "do_commit-and-push", CnaasYamlCliApp.do_commit_and_push)
    del CnaasYamlCliApp.do_commit_and_push
    setattr(CnaasYamlCliApp, "do_get-datamodel", CnaasYamlCliApp.do_get_datamodel)
    del CnaasYamlCliApp.do_get_datamodel
    c = CnaasYamlCliApp()
    sys.exit(c.cmdloop())
