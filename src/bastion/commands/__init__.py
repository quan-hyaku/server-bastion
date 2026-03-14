"""Command group registry."""

from bastion.commands.audit import audit
from bastion.commands.fail2ban import fail2ban
from bastion.commands.firewall import firewall
from bastion.commands.health import health
from bastion.commands.malware import malware
from bastion.commands.nginx import nginx
from bastion.commands.postgres import postgres
from bastion.commands.ssl import ssl
from bastion.commands.tune import tune

ALL_COMMANDS = [health, audit, ssl, nginx, postgres, firewall, fail2ban, malware, tune]
