import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

try:
    deployment_secrets = st.secrets
    for secret_name in (
        "DATABASE_URL",
        "INITIAL_ADMIN_USERNAME",
        "INITIAL_ADMIN_PASSWORD",
        "REQUIRE_DATABASE_URL",
    ):
        if secret_name not in os.environ and secret_name in deployment_secrets:
            os.environ[secret_name] = str(deployment_secrets[secret_name])
except StreamlitSecretNotFoundError:
    deployment_secrets = {}

from data_store import (
    DATABASE_URL,
    EMPLOYEE_TAGS,
    PROPOSAL_STAGES,
    REMARK_OPTIONS,
    SUPER_ADMIN_USERNAME,
    assign_so_to_rm,
    add_record,
    authenticate,
    change_own_password,
    create_account,
    delete_user,
    get_available_records,
    get_admin_records,
    get_daily_commitments,
    get_owned_records,
    get_user,
    get_user_records,
    initialize_database,
    list_users,
    update_admin_phone,
    reset_user_password,
    save_commitment_response,
    update_employee_profile,
    update_proposal,
)

APP_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="UTC+05:30")


def sign_in():
    user = authenticate(
        st.session_state["login_username"],
        st.session_state["login_password"],
    )
    if user:
        st.session_state["authenticated_user_id"] = user["id"]
        st.session_state.pop("login_error", None)
        st.session_state["login_password"] = ""
    else:
        st.session_state["login_error"] = "Invalid username or password."


def sign_out():
    st.session_state.pop("authenticated_user_id", None)
    st.session_state.pop("login_error", None)


def render_copyright_footer():
    st.markdown(
        '<p class="copyright-footer">Copyrights @jeevanreddynimmala</p>',
        unsafe_allow_html=True,
    )


st.set_page_config(
    page_title="Field Operations",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
    :root {
        --ink: #19342e;
        --muted: #667a73;
        --green: #176c59;
        --green-dark: #124c40;
        --line: #d8e3dd;
        --canvas: #f1f5f2;
        --coral: #b85a46;
        --surface: #ffffff;
    }
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    .stApp {
        background: linear-gradient(135deg, #f4f7f4 0%, #edf3ef 100%);
        color: var(--ink);
    }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMainBlockContainer"] {
        max-width: 1480px; padding: 1.35rem 2.5rem 3rem;
    }
    h1, h2, h3 { color: var(--ink); font-family: 'Manrope', sans-serif; }
    h1 { font-size: 2rem; font-weight: 750; line-height: 1.18; }
    h2 { font-size: 1.45rem; font-weight: 700; }
    h3 { font-size: 1.12rem; font-weight: 700; }
    p, label, [data-testid="stCaptionContainer"] { color: var(--muted); }
    .app-masthead {
        display: flex; align-items: center; justify-content: space-between;
        gap: 1rem; padding: .85rem 0 1.25rem; margin-bottom: 1.2rem;
        border-bottom: 1px solid var(--line);
    }
    .brand-lockup { display: flex; align-items: center; gap: .8rem; }
    .brand-glyph {
        display: grid; place-items: center; width: 2.65rem; height: 2.65rem;
        border-radius: 7px; background: var(--green); color: white;
        font: 800 .82rem 'Manrope', sans-serif;
    }
    .brand-name {
        display: block; color: var(--ink); font: 800 .9rem 'Manrope', sans-serif;
    }
    .brand-description { display: block; margin-top: .1rem; color: var(--muted); font-size: .78rem; }
    .masthead-meta {
        display: flex; align-items: center; gap: .5rem; color: var(--muted);
        font-size: .75rem; font-weight: 700;
    }
    .status-dot { width: .48rem; height: .48rem; border-radius: 50%; background: #4c9b73; }
    .eyebrow {
        margin-bottom: .45rem; color: var(--green); font-size: .72rem;
        font-weight: 700; text-transform: uppercase;
    }
    .login-hero {
        display: flex; flex-direction: column; justify-content: center;
        min-height: 22rem; padding: 2.5rem;
        background: linear-gradient(145deg, #ffffff 0%, #edf5f0 100%);
        border: 1px solid var(--line); border-radius: 8px;
        box-shadow: 0 8px 24px rgba(25, 52, 46, .045);
    }
    .login-hero h1 {
        margin: .15rem 0 .9rem; color: var(--ink);
        font-size: clamp(2.15rem, 4vw, 3.2rem); line-height: 1.08;
        letter-spacing: -.045em;
    }
    .login-description {
        max-width: 32rem; margin: 0; color: var(--muted);
        font-size: 1.05rem; line-height: 1.65;
    }
    .login-benefits {
        display: grid; gap: .75rem; margin-top: 2rem;
        color: var(--ink); font-size: .9rem; font-weight: 600;
    }
    .login-benefits span { display: flex; align-items: center; gap: .7rem; }
    .login-benefits span::before {
        content: ""; width: .45rem; height: .45rem; flex: 0 0 .45rem;
        border-radius: 50%; background: var(--green);
    }
    .login-form-caption { margin: -.5rem 0 1rem; }
    .copyright-footer {
        margin: 2rem 0 0; color: var(--muted); font-size: .78rem;
        font-weight: 500;
    }
    .workspace-intro { margin: -.4rem 0 1rem; }
    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] label {
        color: var(--ink); font-size: 1rem; font-weight: 600;
    }
    [data-testid="stSidebar"],
    [data-testid="stSidebarCollapsedControl"] {
        display: none !important;
    }
    [data-testid="stForm"] {
        background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
        padding: 1.25rem 1.35rem;
        box-shadow: 0 8px 24px rgba(25, 52, 46, .045);
    }
    [data-testid="stTextInput"] [data-baseweb="input"],
    [data-testid="stNumberInput"] [data-baseweb="input"],
    [data-testid="stDateInput"] [data-baseweb="input"],
    [data-testid="stTextArea"] [data-baseweb="textarea"],
    [data-testid="stSelectbox"] [data-baseweb="select"] > div {
        background: #ffffff; border: 1px solid #9aada6; border-radius: 6px;
    }
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stDateInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stSelectbox"] [data-baseweb="select"] * {
        color: var(--ink) !important; font-size: 1rem; font-weight: 500;
        -webkit-text-fill-color: var(--ink);
    }
    [class*="st-key-customer_remarks"] [data-baseweb="select"] > div {
        background: #ffffff; border: 1px solid #9aada6; border-radius: 6px;
    }
    [class*="st-key-customer_remarks"] [data-baseweb="select"] * {
        color: var(--ink) !important; font-family: 'DM Sans', sans-serif;
        font-size: 1rem; font-weight: 500;
        -webkit-text-fill-color: var(--ink);
    }
    [class*="st-key-customer_remarks"] [data-baseweb="select"] > div:focus-within {
        border-color: var(--green); box-shadow: 0 0 0 1px var(--green);
    }
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stDateInput"] input,
    [data-testid="stTextArea"] textarea {
        background: #ffffff;
    }
    [data-testid="stTextInput"] [data-baseweb="input"]:focus-within,
    [data-testid="stNumberInput"] [data-baseweb="input"]:focus-within,
    [data-testid="stDateInput"] [data-baseweb="input"]:focus-within,
    [data-testid="stTextArea"] [data-baseweb="textarea"]:focus-within,
    [data-testid="stSelectbox"] [data-baseweb="select"] > div:focus-within {
        border-color: var(--green); box-shadow: 0 0 0 1px var(--green);
    }
    div[data-testid="stMetric"] {
        background: var(--surface); border: 1px solid var(--line); border-radius: 7px;
        padding: 1.05rem 1.15rem; box-shadow: 0 4px 14px rgba(25, 52, 46, .035);
    }
    div[data-testid="stMetricLabel"] { color: var(--muted); }
    div[data-testid="stMetricValue"] { color: var(--ink); font-family: 'Manrope', sans-serif; }
    div.stButton > button, div[data-testid="stFormSubmitButton"] button,
    div[data-testid="stDownloadButton"] button {
        border-radius: 6px; font-weight: 650; min-height: 2.7rem;
    }
    div.stButton > button[kind="primary"],
    div[data-testid="stFormSubmitButton"] button[kind="primary"] {
        background: var(--green); border-color: var(--green); color: white;
    }
    div.stButton > button[kind="primary"]:hover,
    div[data-testid="stFormSubmitButton"] button[kind="primary"]:hover {
        background: var(--green-dark); border-color: var(--green-dark); color: white;
    }
    div.stButton > button:hover, div[data-testid="stFormSubmitButton"] button:hover {
        border-color: var(--green-dark); color: var(--green-dark);
    }
    [data-testid="stTabs"] [role="tablist"] {
        gap: .35rem; border-bottom: 1px solid var(--line);
    }
    [data-testid="stTabs"] [role="tab"] {
        min-height: 2.8rem; color: var(--muted); font-weight: 650;
    }
    [data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: var(--green); }
    [data-testid="stDataFrame"] {
        overflow: hidden; border: 1px solid var(--line); border-radius: 7px;
        background: var(--surface);
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: var(--line) !important; border-radius: 8px !important;
        background: rgba(255, 255, 255, .78);
    }
    header [data-testid="stHeaderActionElements"],
    header [data-testid="stToolbarActions"],
    header [data-testid="stToolbar"],
    header [data-testid="stMainMenu"],
    header [data-testid="stAppDeployButton"],
    header button[title*="Share"],
    header button[aria-label*="Share"],
    header button[title*="Profile"],
    header button[aria-label*="Profile"],
    header button[kind="headerNoPadding"] {
        display: none !important;
    }
    @media (max-width: 640px) {
        [data-testid="stMainBlockContainer"] { padding: .8rem 1rem 2rem; }
        .app-masthead { padding: .55rem 0 1rem; margin-bottom: .8rem; }
        .brand-description { display: none; }
        .masthead-meta { font-size: .65rem; }
        h1 { font-size: 1.65rem; }
        .login-hero { min-height: 0; padding: 1.5rem; }
        .login-hero h1 { font-size: 2.25rem; }
        .login-benefits { gap: .55rem; margin-top: 1.35rem; }
        [data-testid="stMetric"] { padding: .75rem; }
        [data-testid="stForm"] { padding: 1rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if (
    os.environ.get("REQUIRE_DATABASE_URL", "true").casefold() == "true"
    and not DATABASE_URL
):
    st.title("Database configuration required")
    st.error(
        "Set DATABASE_URL to a persistent PostgreSQL database. "
        "For local-only development, set REQUIRE_DATABASE_URL=false."
    )
    st.stop()

if not initialize_database():
    st.title("Administrator setup required")
    st.error(
        f"Configure INITIAL_ADMIN_USERNAME as {SUPER_ADMIN_USERNAME} and set "
        "INITIAL_ADMIN_PASSWORD, then restart the app."
    )
    st.stop()

current_user_id = st.session_state.get("authenticated_user_id")
current_user = get_user(current_user_id) if current_user_id else None

st.markdown(
        """
        <div class="app-masthead">
            <div class="brand-lockup">
                <span class="brand-glyph">FO</span>
                <span>
                    <span class="brand-name">FIELD OPERATIONS</span>
                    <span class="brand-description">Customer acquisition workspace</span>
                </span>
            </div>
            <div class="masthead-meta"><span class="status-dot"></span> UTC+05:30</div>
        </div>
        """,
        unsafe_allow_html=True,
)

if current_user is None:
    login_copy, login_form = st.columns([1.15, 0.85], gap="large")
    with login_copy:
        st.markdown(
            """
            <section class="login-hero">
                <p class="eyebrow">FIELD OPERATIONS</p>
                <h1>Field data,<br>in good hands.</h1>
                <p class="login-description">
                    A secure workspace for capturing customer interest and
                    keeping field teams aligned.
                </p>
                <div class="login-benefits">
                    <span>Structured customer records</span>
                    <span>Role-based team access</span>
                    <span>Clear operational oversight</span>
                </div>
            </section>
            """,
            unsafe_allow_html=True,
        )
    with login_form:
        st.markdown("### Sign in")
        st.markdown(
            '<p class="login-form-caption">Use your assigned account to continue.</p>',
            unsafe_allow_html=True,
        )
        with st.form("login_form"):
            st.text_input("Username", key="login_username")
            st.text_input("Password", type="password", key="login_password")
            st.form_submit_button("Sign in", on_click=sign_in, width="stretch")
        if error := st.session_state.get("login_error"):
            st.error(error)
    render_copyright_footer()
    st.stop()

is_super_admin = current_user["role"] == "super_admin"
is_admin = current_user["role"] in ("admin", "super_admin")
role_label = "Super administrator" if is_super_admin else "Administrator" if is_admin else "Employee"
if not is_admin:
    role_label = f"Employee · {current_user.get('employee_tag') or 'SO'}"

with st.container(border=True):
    account_identity, account_actions = st.columns([3, 2])
    with account_identity:
        st.markdown(f"### {current_user['employee_name']}")
        st.caption(role_label)
    with account_actions:
        password_column, sign_out_column = st.columns([1.3, 1])
        with password_column:
            with st.expander("Change password"):
                with st.form("change_own_password_form", clear_on_submit=True):
                    current_password = st.text_input(
                        "Current password", type="password"
                    )
                    new_own_password = st.text_input(
                        "New password", type="password"
                    )
                    confirm_own_password = st.text_input(
                        "Confirm new password", type="password"
                    )
                    change_password_submitted = st.form_submit_button(
                        "Update password", width="stretch"
                    )
                if change_password_submitted:
                    if new_own_password != confirm_own_password:
                        st.error("The new passwords do not match.")
                    else:
                        try:
                            change_own_password(
                                current_user["id"],
                                current_password,
                                new_own_password,
                            )
                        except ValueError as error:
                            st.error(str(error))
                        else:
                            st.success("Your password has been changed.")
        with sign_out_column:
            st.button(
                "Sign out",
                on_click=sign_out,
                type="secondary",
                width="stretch",
            )

if is_admin:
    managed_users = list_users(current_user["id"])
    managed_records = get_admin_records(current_user["id"])
    employee_count = sum(user["role"] == "user" for user in managed_users)
    administrator_count = sum(user["role"] == "admin" for user in managed_users)
    interested_customer_count = sum(
        record["Remarks"] == "Interested" for record in managed_records
    )
    records_df = pd.DataFrame(managed_records)

    st.markdown(
        '<p class="eyebrow">ADMINISTRATION</p>', unsafe_allow_html=True
    )
    st.title("Operations overview" if is_super_admin else "Team overview")
    st.caption(
        "System-wide access" if is_super_admin else "Your accounts and their submitted records"
    )

    if notice := st.session_state.pop("admin_notice", None):
        st.success(notice)

    overview_tab, accounts_tab, records_tab, commitments_tab = st.tabs(
        ["Overview", "Accounts", "Records", "Commitments"]
    )
    with overview_tab:
        metric_columns = st.columns(3 if is_super_admin else 2)
        metric_columns[0].metric("Employees", employee_count)
        if is_super_admin:
            metric_columns[1].metric("Administrators", administrator_count)
            metric_columns[2].metric(
                "Interested customers", interested_customer_count
            )
        else:
            metric_columns[1].metric(
                "Interested customers", interested_customer_count
            )
        st.caption("Count is based on submitted customer entries.")

        st.subheader("Employee submission summary")
        st.caption("All-time totals and entries submitted yesterday (India Standard Time).")
        yesterday = (datetime.now(APP_TIMEZONE) - timedelta(days=1)).date().isoformat()
        employee_totals = {}
        for record in managed_records:
            employee_name = record["Employee Name"]
            totals = employee_totals.setdefault(
                employee_name,
                {"Total entries": 0, "Interested entries": 0, "Entries yesterday": 0},
            )
            totals["Total entries"] += 1
            totals["Interested entries"] += record["Remarks"] == "Interested"
            totals["Entries yesterday"] += record["Entry Date"] == yesterday

        employee_names = {
            user["employee_name"]
            for user in managed_users
            if user["role"] == "user"
        } | employee_totals.keys()
        employee_summary = [
            {
                "Employee name": employee_name,
                **employee_totals.get(
                    employee_name,
                    {
                        "Total entries": 0,
                        "Interested entries": 0,
                        "Entries yesterday": 0,
                    },
                ),
            }
            for employee_name in sorted(employee_names, key=str.casefold)
        ]
        if employee_summary:
            st.dataframe(
                pd.DataFrame(employee_summary), width="stretch", hide_index=True
            )
        else:
            st.info("No employee accounts or submissions are available in this scope.")

    with accounts_tab:
        create_column, accounts_column = st.columns([0.85, 1.15], gap="large")
        with create_column:
            st.subheader("Create account")
            with st.form("add_account_form", clear_on_submit=True):
                new_username = st.text_input("Username")
                new_employee_name = st.text_input("Employee name")
                if is_super_admin:
                    account_type = st.selectbox(
                        "Account type",
                        options=["Employee", "Administrator"],
                    )
                    account_role = {
                        "Employee": "user",
                        "Administrator": "admin",
                    }[account_type]
                else:
                    st.text_input("Account type", value="Employee", disabled=True)
                    account_role = "user"
                new_phone_number = st.text_input(
                    (
                        "Employee WhatsApp number (international format)"
                        if account_role == "user"
                        else "Administrator WhatsApp number for reports (optional)"
                    ),
                    placeholder="+919876543210",
                )
                new_password = st.text_input("Initial password", type="password")
                add_submitted = st.form_submit_button(
                    "Create account", width="stretch"
                )

            if add_submitted:
                try:
                    create_account(
                        current_user["id"],
                        new_username,
                        new_employee_name,
                        new_password,
                        account_role,
                        new_phone_number,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.session_state["admin_notice"] = (
                        f"{account_type if is_super_admin else 'Employee'} account created."
                        if is_super_admin
                        else "Employee account created."
                    )
                    st.rerun()

        with accounts_column:
            st.subheader("Managed accounts")
            if managed_users:
                users_df = pd.DataFrame(managed_users)
                display_columns = [
                    "username",
                    "employee_name",
                    "employee_tag",
                    "assigned_rm_name",
                    "phone_number",
                    "role",
                    "created_at",
                ]
                if is_super_admin:
                    display_columns.insert(3, "created_by_username")
                users_df = users_df[display_columns].rename(
                    columns={
                        "username": "Username",
                        "employee_name": "Employee name",
                        "created_by_username": "Created by",
                        "created_at": "Created at",
                        "role": "Access level",
                        "employee_tag": "Employee tag",
                        "assigned_rm_name": "Assigned RM",
                        "phone_number": "WhatsApp number",
                    }
                )
                users_df["Access level"] = users_df["Access level"].map(
                    {"user": "Employee", "admin": "Administrator", "super_admin": "Super administrator"}
                )
                st.dataframe(users_df, width="stretch", hide_index=True)
            else:
                st.info("No managed accounts yet.")

        resettable_users = [
            user for user in managed_users if user["id"] != current_user["id"]
        ]
        st.divider()
        st.subheader("Set or reset an account password")
        if is_super_admin:
            st.caption(
                "Set a new password for any employee or administrator. "
                "To change your own password, use Change password above."
            )
        else:
            st.caption("You can reset passwords for employee accounts you created.")
        if resettable_users:
            resettable_users_by_id = {
                user["id"]: user for user in resettable_users
            }
            with st.form("reset_account_password_form", clear_on_submit=True):
                selected_reset_user_id = st.selectbox(
                    "Account",
                    options=list(resettable_users_by_id),
                    format_func=lambda user_id: (
                        f"{resettable_users_by_id[user_id]['employee_name']} · "
                        f"{resettable_users_by_id[user_id]['username']} · "
                        f"{resettable_users_by_id[user_id]['role'].replace('_', ' ').title()}"
                    ),
                )
                new_reset_password = st.text_input(
                    "New password", type="password"
                )
                confirm_reset_password = st.text_input(
                    "Confirm new password", type="password"
                )
                reset_password_submitted = st.form_submit_button(
                    "Set new password", width="stretch"
                )
            if reset_password_submitted:
                if new_reset_password != confirm_reset_password:
                    st.error("The new passwords do not match.")
                else:
                    try:
                        reset_user_password(
                            current_user["id"],
                            selected_reset_user_id,
                            new_reset_password,
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("The account password has been reset.")
        else:
            st.info("There are no other accounts in your scope.")

        employee_users = [user for user in managed_users if user["role"] == "user"]
        if is_super_admin:
            admin_users = [
                user
                for user in managed_users
                if user["role"] == "admin"
            ]
            if admin_users:
                st.divider()
                st.subheader("Update administrator report number")
                admins_by_id = {user["id"]: user for user in admin_users}
                with st.form("admin_phone_form"):
                    selected_admin_id = st.selectbox(
                        "Administrator",
                        options=list(admins_by_id),
                        format_func=lambda user_id: (
                            f"{admins_by_id[user_id]['employee_name']} · "
                            f"{admins_by_id[user_id]['username']}"
                        ),
                    )
                    admin_phone = st.text_input(
                        "WhatsApp number",
                        value=admins_by_id[selected_admin_id].get("phone_number") or "",
                        placeholder="+919876543210",
                    )
                    admin_phone_submitted = st.form_submit_button(
                        "Save administrator number", width="stretch"
                    )
                if admin_phone_submitted:
                    try:
                        update_admin_phone(
                            current_user["id"], selected_admin_id, admin_phone
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("Administrator report number updated.")
                        st.rerun()

        if employee_users:
            st.divider()
            st.subheader("Update employee role and assignment")
            employees_by_id = {user["id"]: user for user in employee_users}
            with st.form("employee_profile_form"):
                selected_employee_id = st.selectbox(
                    "Employee",
                    options=list(employees_by_id),
                    format_func=lambda user_id: (
                        f"{employees_by_id[user_id]['employee_name']} · "
                        f"{employees_by_id[user_id]['username']}"
                    ),
                )
                selected_employee = employees_by_id[selected_employee_id]
                selected_tag = st.selectbox(
                    "Employee tag",
                    options=EMPLOYEE_TAGS,
                    index=EMPLOYEE_TAGS.index(
                        selected_employee.get("employee_tag") or "SO"
                    ),
                )
                updated_phone_number = None
                if is_super_admin:
                    updated_phone_number = st.text_input(
                        "WhatsApp number",
                        value=selected_employee.get("phone_number") or "",
                        placeholder="+919876543210",
                    )
                update_employee_submitted = st.form_submit_button(
                    "Save employee details", width="stretch"
                )
            if update_employee_submitted:
                try:
                    update_employee_profile(
                        current_user["id"],
                        selected_employee_id,
                        employee_tag=selected_tag,
                        phone_number=updated_phone_number,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Employee details updated.")
                    st.rerun()

            so_users = [
                user for user in employee_users if user.get("employee_tag") == "SO"
            ]
            rm_users = [
                user for user in employee_users if user.get("employee_tag") == "RM"
            ]
            if so_users and rm_users:
                st.subheader("Assign SO employees to an RM")
                so_by_id = {user["id"]: user for user in so_users}
                rm_by_id = {user["id"]: user for user in rm_users}
                with st.form("assign_so_to_rm_form"):
                    selected_so_id = st.selectbox(
                        "SO employee",
                        options=list(so_by_id),
                        format_func=lambda user_id: (
                            f"{so_by_id[user_id]['employee_name']} · "
                            f"{so_by_id[user_id]['username']}"
                        ),
                    )
                    current_rm = so_by_id[selected_so_id].get("assigned_rm_id")
                    rm_options = list(rm_by_id)
                    selected_rm_id = st.selectbox(
                        "Assign to RM",
                        options=rm_options,
                        index=rm_options.index(current_rm)
                        if current_rm in rm_options
                        else 0,
                        format_func=lambda user_id: (
                            f"{rm_by_id[user_id]['employee_name']} · "
                            f"{rm_by_id[user_id]['username']}"
                        ),
                    )
                    assignment_submitted = st.form_submit_button(
                        "Save assignment", width="stretch"
                    )
                if assignment_submitted:
                    try:
                        assign_so_to_rm(
                            current_user["id"], selected_so_id, selected_rm_id
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("SO assignment updated.")
                        st.rerun()

        deletable_users = [
            user for user in managed_users if user["id"] != current_user["id"]
        ]
        st.divider()
        st.subheader("Remove account")
        if deletable_users:
            users_by_id = {user["id"]: user for user in deletable_users}
            with st.form("delete_account_form", clear_on_submit=True):
                selected_user_id = st.selectbox(
                    "Account to remove",
                    options=list(users_by_id),
                    format_func=lambda user_id: (
                        f"{users_by_id[user_id]['employee_name']} · "
                        f"{users_by_id[user_id]['username']} · "
                        f"{users_by_id[user_id]['role'].replace('_', ' ').title()}"
                    ),
                )
                confirm_delete = st.checkbox("Confirm account removal")
                delete_submitted = st.form_submit_button(
                    "Delete account", width="stretch"
                )
            if delete_submitted:
                if not confirm_delete:
                    st.error("Confirm the removal before continuing.")
                else:
                    try:
                        delete_user(selected_user_id, current_user["id"])
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.session_state["admin_notice"] = (
                            "Account removed. Its saved records were retained."
                        )
                        st.rerun()
        else:
            st.info("There are no other accounts in your scope.")

    with records_tab:
        st.subheader("All system records" if is_super_admin else "Team records")
        if managed_records:
            download_buffer = BytesIO()
            with pd.ExcelWriter(download_buffer, engine="openpyxl") as writer:
                records_df.to_excel(writer, sheet_name="Field Records", index=False)
            file_prefix = "all_field_records" if is_super_admin else "team_field_records"
            st.download_button(
                "Download Excel",
                data=download_buffer.getvalue(),
                file_name=(
                    f"{file_prefix}_{datetime.now(APP_TIMEZONE).date().isoformat()}.xlsx"
                ),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="content",
            )
            st.dataframe(records_df, width="stretch", hide_index=True)
        else:
            st.info("No records are available in this scope.")
    with commitments_tab:
        st.subheader("Employee commitments")
        commitment_rows = get_daily_commitments(current_user["id"])
        if commitment_rows:
            st.dataframe(
                pd.DataFrame(commitment_rows), width="stretch", hide_index=True
            )
        else:
            st.info("No commitments have been submitted today.")
else:
    st.markdown('<p class="eyebrow">EMPLOYEE WORKSPACE</p>', unsafe_allow_html=True)
    st.title(f"Welcome, {current_user['employee_name']}")
    st.markdown(
        '<p class="workspace-intro">Capture customer interest and review your saved field entries.</p>',
        unsafe_allow_html=True,
    )

    role_tag = current_user.get("employee_tag") or "SO"
    st.caption(f"Employee tag: {role_tag}")
    entry_tab, records_tab, commitment_tab = st.tabs(
        ["New entry", "My records", "Commitments for the day"]
    )
    with entry_tab:
        st.subheader("Capture a customer entry")
        st.caption("Fields marked with * are required.")
        with st.form("data_entry_form", clear_on_submit=True):
            date_column, name_column = st.columns(2)
            with date_column:
                st.date_input(
                    "Entry date",
                    value=datetime.now(APP_TIMEZONE).date(),
                    disabled=True,
                )
            with name_column:
                st.text_input(
                    "Employee name",
                    value=current_user["employee_name"],
                    disabled=True,
                )

            customer_column, contact_column, acres_column, loan_column = st.columns(4)
            with customer_column:
                customer_name = st.text_input("Customer name *")
            with contact_column:
                contact_number = st.text_input("Contact number *", max_chars=15)
            with acres_column:
                acres = st.number_input(
                    "Acres *",
                    min_value=0.0,
                    value=None,
                    step=0.01,
                    format="%.2f",
                )
            with loan_column:
                loan_amount = st.number_input(
                    "Loan amount",
                    min_value=0.0,
                    value=0.0,
                    step=10000.0,
                    format="%.2f",
                )

            village_column, mandal_column, district_column = st.columns(3)
            with village_column:
                village = st.text_input("Village *")
            with mandal_column:
                mandal = st.text_input("Mandal *")
            with district_column:
                district = st.text_input("District *")
            remarks = st.selectbox(
                "Remarks *",
                options=["Select a remark...", *REMARK_OPTIONS],
                key="customer_remarks",
            )
            submitted = st.form_submit_button("Save entry", width="stretch")

        if submitted:
            values = (
                customer_name,
                contact_number,
                village,
                mandal,
                district,
            )
            if (
                not all(value.strip() for value in values)
                or acres is None
                or remarks not in REMARK_OPTIONS
            ):
                st.error("Complete all required fields before saving.")
            else:
                try:
                    entry_timestamp = datetime.now(APP_TIMEZONE)
                    add_record(
                        current_user["id"],
                        entry_timestamp.date().isoformat(),
                        entry_timestamp.strftime("%H:%M:%S"),
                        customer_name.strip(),
                        contact_number.strip(),
                        acres,
                        village.strip(),
                        mandal.strip(),
                        district.strip(),
                        remarks,
                        loan_amount,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Entry saved.")

    with records_tab:
        own_records = get_user_records(current_user["id"])
        if own_records:
            if role_tag == "RM":
                st.caption(
                    "This view includes your proposals and assigned SO proposals. "
                    "Only your own proposals can be updated."
                )
            records_df = pd.DataFrame(own_records)
            download_buffer = BytesIO()
            with pd.ExcelWriter(download_buffer, engine="openpyxl") as writer:
                records_df.to_excel(writer, sheet_name="My Proposals", index=False)
            st.download_button(
                "Download my proposals",
                data=download_buffer.getvalue(),
                file_name=(
                    f"my_proposals_{datetime.now(APP_TIMEZONE).date().isoformat()}.xlsx"
                ),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.dataframe(
                records_df, width="stretch", hide_index=True
            )
            record_by_id = {
                int(record["Proposal ID"]): record
                for record in get_owned_records(current_user["id"])
            }
            if record_by_id:
                st.subheader("Update your own proposal stage or loan amount")
                with st.form("update_proposal_form"):
                    selected_record_id = st.selectbox(
                        "Proposal",
                        options=list(record_by_id),
                        format_func=lambda record_id: (
                            f"#{record_id} · {record_by_id[record_id]['Customer Name']} · "
                            f"{record_by_id[record_id]['Stage']}"
                        ),
                    )
                    selected_record = record_by_id[selected_record_id]
                    stage_index = PROPOSAL_STAGES.index(selected_record["Stage"])
                    allowed_stages = PROPOSAL_STAGES[stage_index : stage_index + 2]
                    proposed_stage = st.selectbox(
                        "Stage",
                        options=allowed_stages,
                        index=0,
                    )
                    proposed_loan_amount = st.number_input(
                        "Loan amount",
                        min_value=0.0,
                        value=float(selected_record["Loan Amount"] or 0),
                        step=10000.0,
                        format="%.2f",
                    )
                    update_proposal_submitted = st.form_submit_button(
                        "Update proposal", width="stretch"
                    )
                if update_proposal_submitted:
                    try:
                        update_proposal(
                            current_user["id"],
                            selected_record_id,
                            stage=proposed_stage,
                            loan_amount=proposed_loan_amount,
                        )
                    except ValueError as error:
                        st.error(str(error))
                    else:
                        st.success("Proposal updated.")
                        st.rerun()
            else:
                st.info("You do not have any proposals of your own to update.")
        else:
            st.info("Your saved entries will appear here.")

    with commitment_tab:
        st.caption(
            "Choose whether you are committing to log a proposal and/or disburse "
            "a proposal today. Login commitments move the selected proposal to Login."
        )
        available_login = get_owned_records(
            current_user["id"], stages=("Yet to login",)
        )
        available_disbursement = get_available_records(
            current_user["id"], stages=("SUD",)
        )
        login_by_id = {
            int(record["Proposal ID"]): record for record in available_login
        }
        disbursement_by_id = {
            int(record["Proposal ID"]): record for record in available_disbursement
        }

        st.subheader("Login commitment")
        with st.form("login_commitment_form"):
            login_answer = st.radio(
                "Will you log a proposal today?",
                options=("Yes", "No"),
                horizontal=True,
                key="login_commitment_answer",
            )
            login_record_id = None
            login_amount = None
            if login_answer == "Yes":
                if login_by_id:
                    login_record_id = st.selectbox(
                        "Proposal to log",
                        options=list(login_by_id),
                        format_func=lambda record_id: (
                            f"#{record_id} · "
                            f"{login_by_id[record_id]['Customer Name']} · "
                            f"{login_by_id[record_id]['Employee Name']}"
                        ),
                        key="login_commitment_record",
                    )
                    login_amount = st.number_input(
                        "Updated loan amount",
                        min_value=0.0,
                        value=float(login_by_id[login_record_id]["Loan Amount"] or 0),
                        step=10000.0,
                        format="%.2f",
                        key="login_commitment_amount",
                    )
                else:
                    st.info("There are no Yet to login proposals available.")
            login_commitment_submitted = st.form_submit_button(
                "Save login commitment", width="stretch"
            )
        if login_commitment_submitted:
            if login_answer == "Yes" and login_record_id is None:
                st.error("There are no proposals available to commit.")
            else:
                try:
                    save_commitment_response(
                        current_user["id"],
                        "Login",
                        login_answer,
                        [login_record_id] if login_record_id is not None else (),
                    )
                    if login_answer == "Yes":
                        update_proposal(
                            current_user["id"],
                            login_record_id,
                            stage="Login",
                            loan_amount=login_amount,
                        )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Login commitment saved.")

        st.subheader("Disbursement commitment")
        with st.form("disbursement_commitment_form"):
            disbursement_answer = st.radio(
                "Will you disburse proposals today?",
                options=("Yes", "No"),
                horizontal=True,
                key="disbursement_commitment_answer",
            )
            selected_disbursement_ids = ()
            if disbursement_answer == "Yes":
                if disbursement_by_id:
                    selected_disbursement_ids = st.multiselect(
                        "SUD proposals to commit",
                        options=list(disbursement_by_id),
                        format_func=lambda record_id: (
                            f"#{record_id} · "
                            f"{disbursement_by_id[record_id]['Customer Name']} · "
                            f"{disbursement_by_id[record_id]['Employee Name']}"
                        ),
                        key="disbursement_commitment_records",
                    )
                else:
                    st.info("There are no SUD proposals available.")
            disbursement_commitment_submitted = st.form_submit_button(
                "Save disbursement commitment", width="stretch"
            )
        if disbursement_commitment_submitted:
            if disbursement_answer == "Yes" and not selected_disbursement_ids:
                st.error("Select at least one SUD proposal for a Yes commitment.")
            else:
                try:
                    save_commitment_response(
                        current_user["id"],
                        "Disbursement",
                        disbursement_answer,
                        selected_disbursement_ids,
                    )
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.success("Disbursement commitment saved.")

render_copyright_footer()
