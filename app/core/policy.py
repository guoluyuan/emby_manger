from typing import Dict, Any

# Keys that must never be cloned from templates
DANGEROUS_POLICY_KEYS = {"IsAdministrator", "IsDisabled", "LoginAttemptsBeforeLockout"}

# Library and parental control buckets
LIBRARY_POLICY_KEYS = {
    "EnableAllFolders",
    "EnabledFolders",
    "ExcludedSubFolders",
    "BlockedMediaFolders",
    "BlockedChannels",
    "EnableAllChannels",
    "EnabledChannels",
}

PARENTAL_POLICY_KEYS = {
    "MaxParentalRating",
    "BlockUnratedItems",
    "BlockedTags",
    "AllowedTags",
}


def clone_policy(
    target_policy: Dict[str, Any],
    src_policy: Dict[str, Any],
    copy_lib: bool = True,
    copy_pol: bool = True,
    copy_par: bool = True,
) -> Dict[str, Any]:
    """
    Clone Emby/Jellyfin policy fields while avoiding dangerous keys.
    Unknown/new keys fall into the 'policy' bucket and will be copied when copy_pol=True.
    """
    for k, v in (src_policy or {}).items():
        if k in DANGEROUS_POLICY_KEYS:
            continue
        is_lib = k in LIBRARY_POLICY_KEYS
        is_par = k in PARENTAL_POLICY_KEYS
        is_pol = not is_lib and not is_par
        if (copy_lib and is_lib) or (copy_par and is_par) or (copy_pol and is_pol):
            target_policy[k] = v
    return target_policy
