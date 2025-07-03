#!/usr/bin/env python
"""A simple cmd2 application."""
import glob
import typing
from copy import copy
from typing import Any
import functools
import os
from io import StringIO

import cmd2
import ruamel.yaml
from git.exc import InvalidGitRepositoryError
from rich.console import Console

from settingsrepo import Settingsrepo
from settings_fields import f_root


yaml = ruamel.yaml.YAML()
yaml.indent(sequence=4, offset=2)
yaml.default_flow_style = False
yaml.preserve_quotes = True

console = Console()


def yaml_key_complete(
    self,
    text: str,
    line: str,
    begidx: int,  # noqa: ARG002
    endidx: int,
    ):
    return ["interfaces"]


#(text, line, begidx)
#if '/' in text:
#    dirpath = os.path.join(os.getcwd(), *text.split("/")[:-1])
#else:
#    dirpath = os.getcwd()
#return [
#    f
#    for f in os.listdir(dirpath)
#    if ((os.path.isfile(f) and f.endswith(".yml")) or os.path.isdir(f)) and f.startswith(
#        text) and not f.startswith(".")
#]


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


def is_list_of_dicts(obj: Any, find_key: str):
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
    return compatible
#    if compatible:
#        for interface in obj:
#            if interface[find_key] == find_key:
#                return interface
#    else:
#        return obj


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


def nested_set(obj, keys, value):
    for key in keys[:-1]:
        if isinstance(obj, dict):
            obj = obj.setdefault(key, {})
        elif isinstance(obj, list):
            obj = obj.setdefault(key, [])
    obj[keys[-1]] = value


class CnaasCliApp(cmd2.Cmd):
    """A simple cmd2 application."""
    complete_cd = functools.partialmethod(cmd2.Cmd.path_complete, path_filter=os.path.isdir)
#    test = cmd2.Cmd.index_based_complete()

    def __init__(self):
        super().__init__()
        self.repo = Settingsrepo(os.getcwd())
        self.prompt = "yaml-cli> "
        self.debug = True
        remove_bulitins = ["do_shell", "do_run_pyscript", "do_run_script", "do_edit", "do_shortcuts", "do_set"]
        setattr(self, "do_cli_set", super(CnaasCliApp, self).do_set)
#        setattr(self, "complete_cli_set", super(CnaasCliApp, self).complete_set_value)
        for cmd in remove_bulitins:
            delattr(cmd2.Cmd, cmd)

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

    def get_next_yaml_item(self, tokens, token, yaml_item):
        try:
            if len(tokens) >= 4 and isinstance(token, str) and token == "interfaces":
                return convert_list_of_dicts(yaml_item[token], "name")
            elif len(tokens) >= 5 and isinstance(token, str) and token == "vrfs":
                return convert_list_of_dicts(yaml_item[token], "name")
            # TODO: neighbor_v4
            else:
                if isinstance(yaml_item, list) and token < len(yaml_item):
                    return yaml_item[token]
                elif isinstance(yaml_item, dict) and token in yaml_item:
                    return yaml_item[token]
        except Exception as e:
            raise e
            if isinstance(yaml_item, dict) and token in yaml_item:
                return yaml_item[token]

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
            return self.path_complete(text, line, begidx, endidx)

        try:
            # first token is the command, second is the file name
            filename = os.path.join(os.getcwd(), tokens[1])
            with open(filename) as f:
                yaml_item = yaml.load(f.read())
                token_path = []
                # if there are more tokens, we need to dig into the yaml
                # structure to find the right list or dict to complete
                for dict_level in range(3, len(tokens)):
                    token = tokens[dict_level-1]
                    if token.isdigit():
                        token = int(token)
                    #TODO:
                    # check if yaml_item[token] is a list of dictionaries where all dictionaries have the key name
                    token_path.append(token)
                    yaml_item = self.get_next_yaml_item(tokens, token, yaml_item)

                ##                print(token_path)
                # if suggest set, walk according to the model fields
                if suggest_set:
                    if len(token_path) == 0:
                        return [cur_match for cur_match in f_root.model_fields.keys() if cur_match.startswith(tokens[index])]
                    current_field = f_root
                    inspect_inner = False
                    print()
                    for idx, token in enumerate(token_path):
                        is_last_token = idx == len(token_path) - 1
                        print(f"DEBUG idx: {idx}, token: {token}, {len(token_path)}, last = {is_last_token}, yaml_item: {str(yaml_item)[:20]}")
                        if idx == 0 and token not in f_root.model_fields:
                            console.log("Unknown setting: {token}")
                            return []
                        elif idx == 0:
                            current_field = f_root.model_fields[token]
                            if current_field.annotation._name == 'Optional':
                                print(f"first optional: {current_field.annotation.__args__[0]}")
                                current_field = current_field.annotation.__args__[0]
#                                if is_last_token:
#                                    return self.complete_last_token(current_field, tokens[index])
#                                if not hasattr(current_field, 'annotation'):
#                                    pass
#                                elif current_field.annotation._name == 'Dict':
#                                    inspect_dict = True
#                                elif current_field.annotation._name == 'List':
#                                    inspect_list = True
                        elif idx > 0:
                            if inspect_inner:
                                current_field = current_field.model_fields[token]
                                inspect_inner = False

#                            print("DEBUG4")
                            print(f"{current_field} type: {type(current_field)}")
#                            print(dir(current_field))
#                            print(type(current_field))

                            if not hasattr(current_field, 'annotation'):
                                # try to complete from yaml data instead of model
                                continue
                            if current_field.annotation == bool:
                                print(f"\n{token}: {current_field.description}")
                                cmd2.cmd2.rl_force_redisplay()
                                return [cur_match for cur_match in ['true', 'false'] if cur_match.startswith(tokens[index])]
                            elif current_field.annotation == str:
                                print(f"\n{token}: {current_field.description}")
                                cmd2.cmd2.rl_force_redisplay()
                                return []
                            elif current_field.annotation == int:
                                print(f"\n{token}: {current_field.description}")
                                cmd2.cmd2.rl_force_redisplay()
                                return []
#                            current_field = current_field.model_fields
                            if current_field.annotation._name == 'Optional':
                                if is_last_token:
                                    print(f"\n{token} (optional): {current_field.description}")
                                    cmd2.cmd2.rl_force_redisplay()
                                current_field = current_field.annotation.__args__[0]
#                                print(f"in optional: {current_field} type: {type(current_field)}")
                                if hasattr(current_field, 'annotation') and current_field.annotation._name in ['Dict', 'List']:
                                    inspect_inner = True
                                if is_last_token:
                                    return self.complete_last_token(current_field, tokens[index])
                            #                                    return [cur_match for cur_match in current_field.model_fields.keys() if cur_match.startswith(tokens[index])]
                            if current_field.annotation._name == 'List':
                                list_of_dicts = is_list_of_dicts_field(current_field)
                                current_field = current_field.annotation.__args__[0]
                                print("DEBUG list")
                                if is_last_token:
                                    # for interfaces, we should complete interface options
                                    #breakpoint()
                                    if not list_of_dicts:
                                        continue
                                    return [cur_match for cur_match in current_field.model_fields.keys() if
                                            cur_match.startswith(tokens[index])]
                                    #continue
                                else:
                                    ### pass
                                    # do inspect interfaces, but not statements?? because interfaces is converted to dict?
                                    print(f"DEBUG5: {token}: {current_field}")
                                    if list_of_dicts:
                                        inspect_inner = True
                            elif current_field.annotation._name == 'Dict':
                                current_field = current_field.annotation.__args__[1]
#                                print("DEBUG dict")
                                if is_last_token:
                                    return [cur_match for cur_match in current_field.model_fields.keys() if cur_match.startswith(tokens[index])]
                                else:
                                    inspect_inner = True
                            else:
                                if is_last_token:
##                                    print("DEBUG2")
                                    return [cur_match for cur_match in current_field.annotation.model_fields.keys() if cur_match.startswith(tokens[index])]
                                else:
                                    current_field = current_field.model_fields[token]
#                            print(isinstance(f_root.model_fields[token_path[0]].annotation, typing.List))
#                            return [cur_match for cur_match in f_root.model_fields[token_path[0]].annotation.__args__[0].model_fields.keys() if cur_match.startswith(tokens[index])]
#                        else:
#                            return []

                    # DEBUG:
                    cmd2.cmd2.rl_force_redisplay()
                if isinstance(yaml_item, list):
                    return [cur_match for cur_match in list(map(str, range(len(yaml_item)))) if cur_match.startswith(tokens[index])]
                elif isinstance(yaml_item, dict):
                    available_options = set(yaml_item.keys())
#                    if suggest_set:
#                        a = f_root.model_fields['interfaces'].annotation.__args__[0]
#                        available_options.update(a.model_fields.keys())
                    return [cur_match for cur_match in available_options if cur_match.startswith(tokens[index])]
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


    def complete_helper(self, text, line, begidx, endidx):
        index_dict = {
            1: self.path_complete,  # Tab complete using path_complete function at index 3 in command line
            2: yaml_key_complete,
        }
        return self.index_based_complete(text, line, begidx, endidx, index_dict=index_dict)

    def do_pull(self, args):
        """Pull settingsrepo"""
        self.repo.pull()

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
        try:
            self.repo = Settingsrepo(os.getcwd())
        except InvalidGitRepositoryError:
            console.log("Not a git repository")

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

            if isinstance(yaml_item, list) and token < len(yaml_item):
                yaml_item = yaml_item[token]
            elif token in yaml_item:
                yaml_item = yaml_item[token]
            if is_list_of_dicts(yaml_item, "name"):
                next_find_dict_key = "name"
        return yaml_item

    def yaml_set_helper(self, argv, yaml_item, set_value=None):
        token_path = []

        next_find_dict_key = None
        next_append_list = False
        new_key = False
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
            except (KeyError, IndexError, TypeError):
                new_key = True
            else:
                # don't update yaml_item for last token, since we want to update mutable object dict/list instead of immutable object str/int/bool
                if dict_level != len(argv) - 1:
                    yaml_item = yaml_item[token]

            if is_list_of_dicts(yaml_item, "name"):
                next_find_dict_key = "name"
            # if last element in yaml is a list, or if pydantic model thinks it should become a list
            elif (isinstance(yaml_item, list) and dict_level == len(argv)-1) or (len(token_path) >= 3 and token_path[0] == "interfaces" and token_path[2] == "tagged_vlan_list"):
                print("Appending to list")
                next_append_list = True
                # if list already exists, update yaml_item so old_value will be old list
                if not new_key and isinstance(yaml_item[token], list):
                    yaml_item = yaml_item[token]

        if set_value.isdigit():
            set_value = int(set_value)
        if new_key:
            old_value = None
        elif next_append_list:
            old_value = copy(yaml_item)
        else:
            old_value = copy(yaml_item[argv[-1]])
        if next_append_list:
            if new_key:
                yaml_item[argv[-1]] = [set_value]
                final_set_value = yaml_item[argv[-1]]
            elif isinstance(yaml_item, list):
                yaml_item.append(set_value)
                final_set_value = yaml_item
        else:
            if token == "config":
                yaml_item[argv[-1]] = ruamel.yaml.scalarstring.LiteralScalarString(set_value)
            else:
                yaml_item[argv[-1]] = set_value
        token_path.append(argv[-1])
        return token_path, old_value, final_set_value

    def complete_set(self, text, line, begidx, endidx):
        return self.settings_complete(text, line, begidx, endidx, suggest_set=True)

    def do_show(self, statement: cmd2.Statement) -> None:
        for filename in glob.glob(os.path.join(os.getcwd(), statement.argv[1])):
            if not os.path.isfile(filename):
                console.log(f"{filename} is not a file")
                continue
            with open(filename) as f:
                text = f.read()
                console.log(f"{filename}:")
                if len(statement.argv) == 2:
                    with console.pager():
                        console.print(text)
                elif len(statement.argv) >= 3:
                    yaml_item = self.yaml_get_helper(statement.argv, text)

                    string_stream = StringIO()
                    yaml.dump(yaml_item, string_stream)
                    yaml_str = string_stream.getvalue()
                    if yaml_str.count('\n') > console.height:
                        with console.pager():
                            console.print(yaml_str)
                    else:
                        console.print(yaml_str)

    def do_set(self, statement: cmd2.Statement) -> None:
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
                    token_path, old_value, final_set_value = self.yaml_set_helper(statement.argv[:-1], yaml_item, set_value)
                    try:
                        f_root(**yaml_item).model_dump()
                    except Exception as e:
                        console.log(f"Error: {e}")
                        continue
                    if old_value == final_set_value:
                        console.log("Value unchanged")
                    else:
                        console.log(f"{' -> '.join([str(x) for x in token_path])} was updated: {old_value} -> [bold red]{final_set_value}[/bold red]")

                    with open(filename, "wb") as f:
                        yaml.dump(yaml_item, f)

    def do_diff(self, statement: cmd2.Statement) -> None:
        print(self.repo.repo.git.diff("--color"))






if __name__ == '__main__':
    import sys
    import os
    #repo = Settingsrepo(os.getcwd())
    #repo.pull()



    c = CnaasCliApp()
    sys.exit(c.cmdloop())
