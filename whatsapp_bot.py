import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request

from data_store import (
    PROPOSAL_STAGES,
    clear_whatsapp_session,
    get_admin_records,
    get_available_records,
    get_daily_commitments,
    get_employee_by_phone,
    get_owned_records,
    get_whatsapp_session,
    get_whatsapp_report_snapshot,
    list_whatsapp_admins,
    list_whatsapp_employees,
    save_commitment_response,
    save_whatsapp_report_snapshot,
    set_whatsapp_session,
    update_proposal,
)

APP_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="UTC+05:30")
GRAPH_VERSION = os.environ.get("WHATSAPP_GRAPH_API_VERSION", "").strip()
PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "").strip()
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "").strip()
MORNING_TEMPLATE = os.environ.get("WHATSAPP_MORNING_TEMPLATE", "").strip()
ADMIN_MORNING_TEMPLATE = os.environ.get(
    "WHATSAPP_ADMIN_MORNING_TEMPLATE", ""
).strip()
EOD_EMPLOYEE_TEMPLATE = os.environ.get(
    "WHATSAPP_EOD_EMPLOYEE_TEMPLATE", ""
).strip()
EOD_TEMPLATE = os.environ.get("WHATSAPP_EOD_TEMPLATE", "").strip()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp_bot")


def _required_configuration():
    missing = [
        name
        for name, value in (
            ("WHATSAPP_GRAPH_API_VERSION", GRAPH_VERSION),
            ("WHATSAPP_PHONE_NUMBER_ID", PHONE_NUMBER_ID),
            ("WHATSAPP_ACCESS_TOKEN", ACCESS_TOKEN),
            ("WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN),
            ("WHATSAPP_APP_SECRET", APP_SECRET),
            ("WHATSAPP_MORNING_TEMPLATE", MORNING_TEMPLATE),
            ("WHATSAPP_ADMIN_MORNING_TEMPLATE", ADMIN_MORNING_TEMPLATE),
            ("WHATSAPP_EOD_EMPLOYEE_TEMPLATE", EOD_EMPLOYEE_TEMPLATE),
            ("WHATSAPP_EOD_TEMPLATE", EOD_TEMPLATE),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing WhatsApp configuration: " + ", ".join(missing)
        )


def _post_meta(payload):
    _required_configuration()
    endpoint = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/"
        f"{PHONE_NUMBER_ID}/messages"
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Meta WhatsApp API returned HTTP {error.code}: {response_body}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Unable to reach the Meta WhatsApp API: {error}") from error
    return json.loads(response_body)


def send_text(phone_number, text):
    return _post_meta(
        {
            "messaging_product": "whatsapp",
            "to": phone_number.lstrip("+"),
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
    )


def send_template(phone_number, template_name, content):
    if len(content) > 1024:
        raise ValueError("WhatsApp template content exceeds the 1024-character limit.")
    return _post_meta(
        {
            "messaging_product": "whatsapp",
            "to": phone_number.lstrip("+"),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en_US"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": content}
                        ],
                    }
                ],
            },
        }
    )


def send_morning_prompts():
    today = datetime.now(APP_TIMEZONE).date().isoformat()
    deadline = _commitment_deadline(time(10, 0))
    sent = 0
    for employee in list_whatsapp_employees():
        send_template(
            employee["phone_number"],
            MORNING_TEMPLATE,
            f"Good morning. Please submit today's login and disbursement "
            f"commitments for {today}. Reply LOGIN YES or LOGIN NO.",
        )
        set_whatsapp_session(
            employee["id"], "login_answer", {"deadline": deadline}
        )
        sent += 1
    logger.info("Sent morning WhatsApp prompts to %s employees", sent)
    for admin in list_whatsapp_admins():
        report = _render_cluster_report(
            admin,
            get_admin_records(admin["id"]),
            get_daily_commitments(admin["id"], today),
            today,
        )
        _send_report(admin["phone_number"], ADMIN_MORNING_TEMPLATE, report)
        save_whatsapp_report_snapshot(
            admin["id"], today, "morning", report
        )


def _commitment_deadline(deadline_time):
    now = datetime.now(APP_TIMEZONE)
    return now.replace(
        hour=deadline_time.hour,
        minute=deadline_time.minute,
        second=0,
        microsecond=0,
    ).isoformat()


def _render_cluster_report(admin, records, commitments, today):
    today_records = [record for record in records if record["Entry Date"] == today]
    achieved = [
        record
        for record in records
        if record["Stage"] == "Disbursement"
        and (record["Stage Updated At"] or "").startswith(today)
    ]
    employee_counts = {}
    employee_disbursements = {}
    for record in today_records:
        employee_counts.setdefault(record["Employee Name"], 0)
        employee_counts[record["Employee Name"]] += 1
    for record in achieved:
        employee_disbursements.setdefault(record["Employee Name"], 0)
        employee_disbursements[record["Employee Name"]] += 1
    lines = [
        f"Cluster: {admin['employee_name']}",
        f"Entries today: {len(today_records)}",
        f"Disbursements achieved today: {len(achieved)}",
        "Disbursement loan amount: "
        + f"{sum(float(record['Loan Amount'] or 0) for record in achieved):,.2f}",
    ]
    if employee_counts:
        lines.append("Entries by employee:")
        lines.extend(
            f"{name}: {count} entries, "
            f"{employee_disbursements.get(name, 0)} disbursements"
            for name, count in sorted(employee_counts.items(), key=lambda item: item[0].casefold())
        )
    if commitments:
        lines.append("Commitments:")
        for item in commitments:
            proposal = (
                f" - {item['Customer']} ({item['Proposal owner']}), "
                f"loan {float(item['Loan amount'] or 0):,.2f}"
                if item["Customer"]
                else ""
            )
            lines.append(
                f"{item['Employee name']} {item['Commitment type']}: "
                f"{item['Answer']}{proposal}"
            )
    else:
        lines.append("Commitments: none submitted")
    return "\n".join(lines)


def _send_report(phone_number, template_name, report):
    lines = report.splitlines()
    chunks = []
    current = ""
    for line in lines:
        if len(line) > 900:
            raise ValueError("A WhatsApp report line exceeds its safe message length.")
        if current and len(current) + len(line) + 1 > 900:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for index, chunk in enumerate(chunks, start=1):
        message = f"Part {index}/{len(chunks)}\n{chunk}" if len(chunks) > 1 else chunk
        send_template(phone_number, template_name, message)


def send_eod_reports():
    today = datetime.now(APP_TIMEZONE).date().isoformat()
    sent = 0
    for admin in list_whatsapp_admins():
        records = get_admin_records(admin["id"])
        commitments = get_daily_commitments(admin["id"], today)
        report = _render_cluster_report(admin, records, commitments, today)
        _send_report(admin["phone_number"], EOD_TEMPLATE, report)
        save_whatsapp_report_snapshot(
            admin["id"], today, "evening", report
        )
        sent += 1
    logger.info("Sent end-of-day WhatsApp reports to %s administrators", sent)
    deadline = _commitment_deadline(time(20, 30))
    for employee in list_whatsapp_employees():
        send_template(
            employee["phone_number"],
            EOD_EMPLOYEE_TEMPLATE,
            f"Good evening. Review or resubmit today's commitments by "
            f"8:30 PM. Reply LOGIN YES or LOGIN NO.",
        )
        set_whatsapp_session(
            employee["id"], "resubmit", {"deadline": deadline}
        )


def send_consolidated_updates(report_type, template_name):
    today = datetime.now(APP_TIMEZONE).date().isoformat()
    sent = 0
    for admin in list_whatsapp_admins():
        report = _render_cluster_report(
            admin,
            get_admin_records(admin["id"]),
            get_daily_commitments(admin["id"], today),
            today,
        )
        previous_report = get_whatsapp_report_snapshot(
            admin["id"], today, report_type
        )
        if previous_report == report:
            continue
        heading = (
            "Updated morning consolidated report"
            if report_type == "morning"
            else "Updated evening consolidated report"
        )
        _send_report(
            admin["phone_number"], template_name, f"{heading}\n{report}"
        )
        save_whatsapp_report_snapshot(
            admin["id"], today, report_type, report
        )
        sent += 1
    logger.info(
        "Sent %s changed consolidated reports to %s administrators",
        report_type,
        sent,
    )


def _available_proposals(employee_id, stage):
    return get_available_records(employee_id, stages=(stage,))


def _owned_proposals(employee_id, stage):
    return get_owned_records(employee_id, stages=(stage,))


def _proposal_list(records):
    return "\n".join(
        f"#{record['Proposal ID']} - {record['Customer Name']} "
        f"({record['Employee Name']}, loan {record['Loan Amount']})"
        for record in records
    )


def _start_disbursement_prompt(employee_id, phone_number, deadline):
    set_whatsapp_session(
        employee_id, "disbursement_answer", {"deadline": deadline}
    )
    send_text(
        phone_number,
        "Will you commit to disburse today? Reply DISBURSEMENT YES or "
        "DISBURSEMENT NO.",
    )


def _handle_employee_message(employee_id, phone_number, message_text):
    message = message_text.strip()
    session = get_whatsapp_session(employee_id)
    state = session["state"] if session else "ready"
    payload = session["payload"] if session else {}

    stage_command = re.fullmatch(
        r"STAGE\s+(\d+)\s+(.+)", message, flags=re.IGNORECASE
    )
    if stage_command:
        stage_name = stage_command.group(2).strip()
        stage = next(
            (
                option
                for option in PROPOSAL_STAGES
                if option.casefold() == stage_name.casefold()
            ),
            None,
        )
        if not stage:
            send_text(phone_number, "Unknown stage. Use one of: " + ", ".join(PROPOSAL_STAGES))
            return
        update_proposal(employee_id, int(stage_command.group(1)), stage=stage)
        send_text(phone_number, "Proposal stage updated.")
        return

    amount_command = re.fullmatch(
        r"AMOUNT\s+(\d+)\s+(\d+(?:\.\d{1,2})?)", message, flags=re.IGNORECASE
    )
    if amount_command:
        update_proposal(
            employee_id,
            int(amount_command.group(1)),
            loan_amount=float(amount_command.group(2)),
        )
        send_text(phone_number, "Proposal loan amount updated.")
        return

    normalized = message.upper()
    if state == "resubmit":
        if normalized in ("LOGIN YES", "LOGIN NO"):
            state = "login_answer"
            set_whatsapp_session(employee_id, state, payload)
        elif normalized in ("DISBURSEMENT YES", "DISBURSEMENT NO"):
            state = "disbursement_answer"
            set_whatsapp_session(employee_id, state, payload)
        else:
            send_text(
                phone_number,
                "You can resubmit LOGIN YES/NO or DISBURSEMENT YES/NO "
                "before the commitment window closes.",
            )
            return

    deadline = payload.get("deadline")
    if deadline and datetime.now(APP_TIMEZONE) > datetime.fromisoformat(deadline):
        clear_whatsapp_session(employee_id)
        send_text(phone_number, "The commitment resubmission window has closed.")
        return

    if state == "login_answer":
        if normalized in ("LOGIN NO", "NO"):
            save_commitment_response(employee_id, "Login", "No")
            _start_disbursement_prompt(
                employee_id, phone_number, payload.get("deadline")
            )
        elif normalized in ("LOGIN YES", "YES"):
            proposals = _owned_proposals(employee_id, "Yet to login")
            if not proposals:
                send_text(phone_number, "No Yet to login proposals are available.")
                _start_disbursement_prompt(
                    employee_id, phone_number, payload.get("deadline")
                )
                return
            set_whatsapp_session(
                employee_id,
                "login_select",
                {"deadline": payload.get("deadline")},
            )
            send_text(
                phone_number,
                "Reply with one proposal ID to commit for login:\n"
                + _proposal_list(proposals),
            )
        else:
            send_text(phone_number, "Reply LOGIN YES or LOGIN NO.")
    elif state == "login_select":
        if not message.isdigit():
            send_text(phone_number, "Reply with one numeric proposal ID.")
            return
        proposal_id = int(message)
        proposals = _owned_proposals(employee_id, "Yet to login")
        if proposal_id not in {record["Proposal ID"] for record in proposals}:
            send_text(phone_number, "That proposal is not available for login.")
            return
        set_whatsapp_session(
            employee_id,
            "login_amount",
            {
                "proposal_id": proposal_id,
                "deadline": payload.get("deadline"),
            },
        )
        send_text(
            phone_number,
            f"Reply with the updated loan amount for proposal #{proposal_id}.",
        )
    elif state == "login_amount":
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", message):
            send_text(phone_number, "Enter a non-negative numeric loan amount.")
            return
        try:
            loan_amount = float(message)
        except ValueError:
            send_text(phone_number, "Enter a non-negative numeric loan amount.")
            return
        if loan_amount < 0:
            send_text(phone_number, "Loan amount cannot be negative.")
            return
        proposal_id = int(payload["proposal_id"])
        save_commitment_response(employee_id, "Login", "Yes", [proposal_id])
        update_proposal(
            employee_id, proposal_id, stage="Login", loan_amount=loan_amount
        )
        _start_disbursement_prompt(
            employee_id, phone_number, payload.get("deadline")
        )
    elif state == "disbursement_answer":
        if normalized in ("DISBURSEMENT NO", "NO"):
            save_commitment_response(employee_id, "Disbursement", "No")
            set_whatsapp_session(
                employee_id, "resubmit", {"deadline": payload.get("deadline")}
            )
            send_text(
                phone_number,
                "Today's commitments are saved. You can resubmit until "
                + datetime.fromisoformat(payload["deadline"]).strftime("%I:%M %p")
                + ".",
            )
        elif normalized in ("DISBURSEMENT YES", "YES"):
            proposals = _available_proposals(employee_id, "SUD")
            if not proposals:
                send_text(phone_number, "No SUD proposals are available.")
                set_whatsapp_session(
                    employee_id, "resubmit", {"deadline": payload.get("deadline")}
                )
                return
            set_whatsapp_session(
                employee_id,
                "disbursement_select",
                {"deadline": payload.get("deadline")},
            )
            send_text(
                phone_number,
                "Reply with one or more proposal IDs separated by commas:\n"
                + _proposal_list(proposals),
            )
        else:
            send_text(phone_number, "Reply DISBURSEMENT YES or DISBURSEMENT NO.")
    elif state == "disbursement_select":
        if not re.fullmatch(r"\d+(?:\s*,\s*\d+)*", message):
            send_text(phone_number, "Reply with proposal IDs separated by commas.")
            return
        proposal_ids = [int(value.strip()) for value in message.split(",")]
        proposals = _available_proposals(employee_id, "SUD")
        available_ids = {record["Proposal ID"] for record in proposals}
        if not set(proposal_ids).issubset(available_ids):
            send_text(phone_number, "One or more proposals are not available at SUD.")
            return
        save_commitment_response(
            employee_id, "Disbursement", "Yes", proposal_ids
        )
        set_whatsapp_session(
            employee_id, "resubmit", {"deadline": payload.get("deadline")}
        )
        send_text(
            phone_number,
            "Today's commitments are saved. You can resubmit until "
            + datetime.fromisoformat(payload["deadline"]).strftime("%I:%M %p")
            + ".",
        )
    else:
        send_text(
            phone_number,
            "Use LOGIN YES/NO for login commitments, "
            "DISBURSEMENT YES/NO for disbursement commitments, "
            "STAGE <proposal ID> <stage>, or AMOUNT <proposal ID> <amount>.",
        )


def _handle_webhook_payload(payload):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                phone_number = "+" + message.get("from", "")
                employee = get_employee_by_phone(phone_number)
                if not employee:
                    logger.warning("Ignoring WhatsApp message from an unknown employee")
                    continue
                text = message.get("text", {}).get("body", "")
                if message.get("type") == "interactive":
                    reply = message.get("interactive", {}).get("button_reply", {})
                    text = reply.get("title") or message.get(
                        "interactive", {}
                    ).get("list_reply", {}).get("title", "")
                if not text:
                    send_text(
                        phone_number,
                        "Please reply with a text command for your commitment.",
                    )
                    continue
                try:
                    _handle_employee_message(employee["id"], phone_number, text)
                except ValueError as error:
                    send_text(phone_number, str(error))


async def _scheduler():
    sent_morning = None
    sent_morning_update = None
    sent_eod = None
    sent_eod_update = None
    while True:
        now = datetime.now(APP_TIMEZONE)
        today = now.date()
        try:
            if (
                now.hour == 9
                and now.minute in (30, 31)
                and sent_morning != today
            ):
                send_morning_prompts()
                sent_morning = today
            if (
                now.hour == 10
                and now.minute in (0, 1)
                and sent_morning_update != today
            ):
                send_consolidated_updates("morning", ADMIN_MORNING_TEMPLATE)
                sent_morning_update = today
            if (
                now.hour == 20
                and now.minute in (0, 1)
                and sent_eod != today
            ):
                send_eod_reports()
                sent_eod = today
            if (
                now.hour == 20
                and now.minute in (30, 31)
                and sent_eod_update != today
            ):
                send_consolidated_updates("evening", EOD_TEMPLATE)
                sent_eod_update = today
        except (RuntimeError, ValueError):
            logger.exception("Scheduled WhatsApp delivery failed")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_scheduler())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/webhook")
async def verify_webhook(request: Request):
    if request.query_params.get("hub.mode") != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid webhook mode.")
    if request.query_params.get("hub.verify_token") != VERIFY_TOKEN:
        raise HTTPException(status_code=403, detail="Webhook verification failed.")
    challenge = request.query_params.get("hub.challenge")
    if challenge is None:
        raise HTTPException(status_code=400, detail="Missing webhook challenge.")
    return int(challenge)


@app.post("/webhook")
async def receive_webhook(request: Request):
    _required_configuration()
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    expected_signature = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.") from error
    _handle_webhook_payload(payload)
    return {"status": "received"}
