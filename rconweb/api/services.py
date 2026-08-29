import os
from http.client import CannotSendRequest
from logging import getLogger
from xmlrpc.client import Fault, ServerProxy

from django.views.decorators.csrf import csrf_exempt

from rcon.discord import send_to_discord_audit

from .audit_log import record_audit
from .auth import api_response, login_required
from .decorators import permission_required, require_content_type, require_http_methods
from .utils import _get_data

logger = getLogger(__name__)

supervisor_client = None
worker_launcher_client = None


def get_supervisor_client():
    global supervisor_client

    if not supervisor_client:
        url = os.getenv("SUPERVISOR_RPC_URL")
        if not url:
            raise ValueError("Can't start services, the url of supervisor isn't set")
        supervisor_client = ServerProxy(url)

    return supervisor_client


def get_worker_launcher_client():
    global worker_launcher_client

    if not worker_launcher_client:
        url = os.getenv("WORKER_LAUNCHER_RPC_URL")
        if not url:
            raise ValueError(
                "Can't reach worker_launcher, WORKER_LAUNCHER_RPC_URL isn't set"
            )
        worker_launcher_client = ServerProxy(url)

    return worker_launcher_client


@csrf_exempt
@login_required()
@permission_required("api.can_view_available_services", raise_exception=True)
@require_http_methods(["GET"])
def get_services(request):
    info = {
        "cron": "The scheduler, cleans logs and whatever you added.",
    }
    client = get_supervisor_client()

    try:
        processes = client.supervisor.getAllProcessInfo()
        result = [
            dict(info=info.get(p["name"], ""), source="supervisord", **p)
            for p in processes
        ]
    except:  # noqa
        if os.getenv("DJANGO_DEBUG"):
            result = []
        else:
            raise

    try:
        launcher_client = get_worker_launcher_client()
        children = launcher_client.getAllChildInfo()
        result += [
            dict(info=c.get("description", ""), source="worker_launcher", **c)
            for c in children
        ]
    except:  # noqa
        # worker_launcher's control server isn't reachable (e.g. it hasn't
        # started yet, or this deployment predates it) - degrade to
        # showing only the real supervisord-managed programs rather than
        # failing the whole page.
        logger.exception("Could not reach worker_launcher RPC server")

    return api_response(
        result=result,
        command="get_services",
        failed=False,
    )


def _supervisor_restart(client, service_name):
    # supervisord's RPC has no native restart call - stop then start,
    # matching what supervisorctl's own "restart" command does internally.
    try:
        client.supervisor.stopProcess(service_name)
    except Fault:
        pass  # already stopped - fine, we're about to start it anyway
    return client.supervisor.startProcess(service_name)


@csrf_exempt
@login_required()
@permission_required("api.can_toggle_services", raise_exception=True)
@record_audit
@require_http_methods(["POST"])
@require_content_type()
def do_service(request):
    data = _get_data(request)
    error = None
    res = None
    command_name = "do_service"
    failed = False

    action = data.get("action")
    service_name = data.get("service_name")
    # Defaults to supervisord for any client that predates this field -
    # every row the current frontend renders echoes back the source it
    # was given by get_services.
    source = data.get("source", "supervisord")

    if source == "worker_launcher":
        client = get_worker_launcher_client()
        actions = {
            "START": client.startChild,
            "STOP": client.stopChild,
            "RESTART": client.restartChild,
        }
    else:
        client = get_supervisor_client()
        actions = {
            "START": client.supervisor.startProcess,
            "STOP": client.supervisor.stopProcess,
            "RESTART": lambda name: _supervisor_restart(client, name),
        }

    if not action or action.upper() not in actions:
        failed = True
        return api_response(
            command=command_name,
            arguments=data,
            failed=failed,
            error="action must be START, STOP, or RESTART",
            status_code=400,
        )
    if not service_name:
        failed = True
        return api_response(
            command=command_name,
            arguments=data,
            failed=failed,
            error="service_name must be set",
            status_code=400,
        )

    try:
        res = actions[action.upper()](service_name)
        send_to_discord_audit(
            message=f"{service_name} {action}",
            command_name=f"service {action}",
            by=request.user.username,
        )
    except CannotSendRequest:
        error = "Service request already sent"
        logger.info(f"{error=}")
    except Fault as e:
        error = repr(e)

    return api_response(
        command=command_name,
        arguments=data,
        failed=failed,
        result=res,
        error=error,
    )
