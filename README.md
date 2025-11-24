## CNaaS YAML CLI

A CLI interface for doing auto-completion of YAML items in a CNaaS settings repository

Example run/install script:

```
#!/bin/bash

CYCPATH="$HOME/.cnaas_yaml_cli"

# if directory .cnaas_yaml_cli exists
if [ -d $CYCPATH ]; then
  $CYCPATH/bin/python3 -m venv --upgrade $CYCPATH
else
  # ask to continue y/n, save in variable REPLY
  read -p "venv $CYCPATH does not exist, create? [y/N] " -n 1 -r
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    python3 -m venv $CYCPATH
    $CYCPATH/bin/pip3 -q install cnaas_yaml_cli
  fi
fi

$CYCPATH/bin/python3 $CYCPATH/lib/python*/site-packages/cnaas_yaml_cli/cli.py
```
