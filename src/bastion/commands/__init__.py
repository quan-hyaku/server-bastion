"""Command group registry."""

from bastion.commands.fail2ban import fail2ban
from bastion.commands.firewall import firewall
from bastion.commands.nginx import nginx
from bastion.commands.postgres import postgres
from bastion.commands.tune import tune

ALL_COMMANDS = [nginx, postgres, firewall, fail2ban, tune]
