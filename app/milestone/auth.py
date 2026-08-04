LOGIN_TYPE_BASIC = "Basic"
LOGIN_TYPE_ACTIVE_DIRECTORY = "ActiveDirectory"


def normalize_login_type(value: str) -> str:
    normalized = value.strip().replace("_", "").replace("-", "").casefold()
    login_types = {
        "basic": LOGIN_TYPE_BASIC,
        "activedirectory": LOGIN_TYPE_ACTIVE_DIRECTORY,
        "ad": LOGIN_TYPE_ACTIVE_DIRECTORY,
        "windows": LOGIN_TYPE_ACTIVE_DIRECTORY,
    }

    try:
        return login_types[normalized]
    except KeyError as exc:
        raise ValueError(
            "MILESTONE_LOGIN_TYPE must be Basic or ActiveDirectory"
        ) from exc


def build_login_username(login_type: str, domain: str, username: str) -> str:
    if login_type == LOGIN_TYPE_BASIC:
        return username

    if domain and "\\" not in username:
        return f"{domain}\\{username}"

    return username
