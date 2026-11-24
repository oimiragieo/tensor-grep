//! Pure Windows managed-runtime trust policy; OS identity and ACL parsing are caller-owned.
//! Callers provide verified nonempty SID strings and effective known allow grants. Pass `None`
//! for a NULL DACL or any unavailable/unparsed ACL; an unknown ACE must fail in the parser.

const WIN_WRITE_MASK: u32 = 0x500D0156;
const WIN_REPLACE_MASK: u32 = 0x100C0040;
const SID_SYSTEM: &str = "S-1-5-18";
const SID_ADMINISTRATORS: &str = "S-1-5-32-544";
const SID_CREATOR_OWNER: &str = "S-1-3-0";
const SID_OWNER_RIGHTS: &str = "S-1-3-4";
const SID_TRUSTED_INSTALLER: &str =
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
const SID_EVERYONE: &str = "S-1-1-0";
const SID_USERS: &str = "S-1-5-32-545";
const SID_AUTHENTICATED_USERS: &str = "S-1-5-11";

/// Trust an ancestor directory using the verified owner and effective allow grants.
/// A NULL, unavailable, or unparsed DACL must be passed as `None` and is rejected.
pub fn ancestor_is_trusted(owner: &str, grants: Option<&[(String, u32)]>, user_sid: &str) -> bool {
    if user_sid.is_empty()
        || !(owner == user_sid
            || owner == SID_SYSTEM
            || owner == SID_ADMINISTRATORS
            || owner == SID_TRUSTED_INSTALLER)
    {
        return false;
    }
    let Some(grants) = grants else {
        return false;
    };
    grants.iter().all(|(sid, mask)| {
        !(is_broad_sid(sid) && mask & WIN_WRITE_MASK != 0)
            && (sid == user_sid || is_trusted_grant_sid(sid) || mask & WIN_REPLACE_MASK == 0)
    })
}

/// Trust managed directories; trusted system owners and token default owners are accepted.
/// A NULL, unavailable, or unparsed DACL must be passed as `None` and is rejected.
pub fn parent_is_trusted(
    owner: &str,
    grants: Option<&[(String, u32)]>,
    user_sid: &str,
    token_owner_sid: Option<&str>,
) -> bool {
    if !valid_managed_owner(owner, user_sid, token_owner_sid) {
        return false;
    }
    let Some(grants) = grants else {
        return false;
    };
    managed_grants_are_safe(grants, user_sid)
}

/// Trust grants on a legacy parent without checking its owner.
pub fn parent_grants_are_trusted(grants: Option<&[(String, u32)]>, user_sid: &str) -> bool {
    !user_sid.is_empty() && grants.is_some_and(|grants| managed_grants_are_safe(grants, user_sid))
}

/// Trust a non-secret executable, allowing foreign read-only grants.
/// A NULL, unavailable, or unparsed DACL must be passed as `None` and is rejected.
pub fn integrity_file_is_trusted(
    owner: &str,
    grants: Option<&[(String, u32)]>,
    user_sid: &str,
    token_owner_sid: Option<&str>,
) -> bool {
    if !valid_managed_owner(owner, user_sid, token_owner_sid) {
        return false;
    }
    let Some(grants) = grants else {
        return false;
    };
    managed_grants_are_safe(grants, user_sid)
}

/// Trust the selector file; trusted system owners and token default owners are accepted,
/// and foreign grants must be read-only.
/// A NULL, unavailable, or unparsed DACL must be passed as `None` and is rejected.
pub fn selector_is_trusted(
    owner: &str,
    grants: Option<&[(String, u32)]>,
    user_sid: &str,
    token_owner_sid: Option<&str>,
) -> bool {
    if !valid_managed_owner(owner, user_sid, token_owner_sid) {
        return false;
    }
    let Some(grants) = grants else {
        return false;
    };
    managed_grants_are_safe(grants, user_sid)
}

/// Describe why an ACL is untrusted (untrusted owner, NULL/unavailable DACL, or offending foreign grants).
pub fn describe_untrusted_grants(
    owner: &str,
    grants: Option<&[(String, u32)]>,
    user_sid: &str,
    token_owner_sid: Option<&str>,
) -> String {
    let mut reasons = Vec::new();
    if !valid_managed_owner(owner, user_sid, token_owner_sid) {
        reasons.push(format!("untrusted owner {owner} (expected {user_sid})"));
    }
    match grants {
        None => reasons.push("DACL is NULL or unavailable".to_owned()),
        Some(grants) => {
            let offending: Vec<String> = grants
                .iter()
                .filter(|(sid, mask)| !grant_is_safe(sid, *mask, user_sid))
                .map(|(sid, mask)| format!("{sid}=0x{mask:08x}"))
                .collect();
            if !offending.is_empty() {
                reasons.push(format!("untrusted grants: {}", offending.join(", ")));
            }
        }
    }
    if reasons.is_empty() {
        "trusted".to_owned()
    } else {
        reasons.join("; ")
    }
}

fn valid_owner(owner: &str, user_sid: &str, token_owner_sid: Option<&str>) -> bool {
    !owner.is_empty()
        && !user_sid.is_empty()
        && (owner == user_sid
            || token_owner_sid
                .is_some_and(|token_owner| !token_owner.is_empty() && owner == token_owner))
}

fn valid_managed_owner(owner: &str, user_sid: &str, token_owner_sid: Option<&str>) -> bool {
    !user_sid.is_empty()
        && (valid_owner(owner, user_sid, token_owner_sid)
            || owner == SID_SYSTEM
            || owner == SID_ADMINISTRATORS
            || owner == SID_TRUSTED_INSTALLER)
}

fn grant_is_safe(sid: &str, mask: u32, user_sid: &str) -> bool {
    sid == user_sid || is_trusted_grant_sid(sid) || mask & WIN_WRITE_MASK == 0
}

fn managed_grants_are_safe(grants: &[(String, u32)], user_sid: &str) -> bool {
    grants
        .iter()
        .all(|(sid, mask)| grant_is_safe(sid, *mask, user_sid))
}

fn is_trusted_grant_sid(sid: &str) -> bool {
    sid == SID_SYSTEM
        || sid == SID_ADMINISTRATORS
        || sid == SID_CREATOR_OWNER
        || sid == SID_OWNER_RIGHTS
}

fn is_broad_sid(sid: &str) -> bool {
    sid == SID_EVERYONE || sid == SID_USERS || sid == SID_AUTHENTICATED_USERS
}

#[cfg(test)]
mod tests {
    use super::{
        ancestor_is_trusted, describe_untrusted_grants, integrity_file_is_trusted,
        parent_grants_are_trusted, parent_is_trusted, selector_is_trusted, WIN_REPLACE_MASK,
        WIN_WRITE_MASK,
    };

    const USER: &str = "S-1-5-21-111-222-333-1001";
    const OTHER: &str = "S-1-5-21-444-555-666-1001";
    const SYSTEM: &str = "S-1-5-18";
    const ADMIN: &str = "S-1-5-32-544";
    const EVERYONE: &str = "S-1-1-0";
    const CREATOR_OWNER: &str = "S-1-3-0";
    const OWNER_RIGHTS: &str = "S-1-3-4";
    const USERS: &str = "S-1-5-32-545";
    const AUTHENTICATED_USERS: &str = "S-1-5-11";
    const TRUSTED_INSTALLER: &str =
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";

    #[test]
    fn ancestor_default_owner_and_ordinary_grants_are_trusted() {
        let grants = [(SYSTEM.to_owned(), 0x1)];

        let trusted = ancestor_is_trusted(USER, Some(&grants), USER);

        assert!(trusted);
    }

    #[test]
    fn ancestor_broad_write_grant_is_refused() {
        let grants = [(EVERYONE.to_owned(), 0x2)];

        let trusted = ancestor_is_trusted(USER, Some(&grants), USER);

        assert!(!trusted);
    }

    #[test]
    fn ancestor_foreign_replace_grant_is_refused() {
        let grants = [(OTHER.to_owned(), 0x40)];

        let trusted = ancestor_is_trusted(USER, Some(&grants), USER);

        assert!(!trusted);
    }

    #[test]
    fn ancestor_foreign_delete_without_delete_child_is_allowed() {
        let grants = [(OTHER.to_owned(), 0x10000)];

        let trusted = ancestor_is_trusted(USER, Some(&grants), USER);

        assert!(trusted);
    }

    #[test]
    fn ancestor_trustedinstaller_owner_is_allowed() {
        let trusted = ancestor_is_trusted(TRUSTED_INSTALLER, Some(&[]), USER);

        assert!(trusted);
    }

    #[test]
    fn ancestor_null_dacl_is_refused() {
        let trusted = ancestor_is_trusted(USER, None, USER);

        assert!(!trusted);
    }

    #[test]
    fn parent_foreign_read_grant_is_allowed() {
        let grants = [(OTHER.to_owned(), 0x1)];

        let trusted = parent_is_trusted(USER, Some(&grants), USER, None);

        assert!(trusted);
    }

    #[test]
    fn parent_foreign_write_grant_is_refused() {
        let grants = [(OTHER.to_owned(), 0x2)];

        let trusted = parent_is_trusted(USER, Some(&grants), USER, None);

        assert!(!trusted);
    }

    #[test]
    fn parent_administrator_owner_with_foreign_read_grant_is_allowed() {
        let grants = [(USER.to_owned(), 0x1F01FF), (OTHER.to_owned(), 0x001200A9)];

        assert!(parent_is_trusted(ADMIN, Some(&grants), USER, None));
    }

    #[test]
    fn parent_token_default_administrator_owner_is_allowed() {
        let trusted = parent_is_trusted(ADMIN, Some(&[]), USER, Some(ADMIN));

        assert!(trusted);
    }

    #[test]
    fn parent_creator_owner_grant_is_allowed() {
        let grants = [(CREATOR_OWNER.to_owned(), 0x2)];

        let trusted = parent_is_trusted(USER, Some(&grants), USER, None);

        assert!(trusted);
    }

    #[test]
    fn parent_null_dacl_is_refused() {
        let trusted = parent_is_trusted(USER, None, USER, None);

        assert!(!trusted);
    }

    #[test]
    fn selector_foreign_read_grant_is_allowed() {
        let grants = [(OTHER.to_owned(), 0x1)];

        let trusted = selector_is_trusted(USER, Some(&grants), USER, None);

        assert!(trusted);
    }

    #[test]
    fn integrity_file_and_selector_allow_foreign_read_execute_grant() {
        let grants = [(USER.to_owned(), 0x1F01FF), (OTHER.to_owned(), 0x001200A9)];

        assert!(integrity_file_is_trusted(USER, Some(&grants), USER, None));
        assert!(selector_is_trusted(USER, Some(&grants), USER, None));
    }

    #[test]
    fn integrity_file_foreign_write_grant_is_refused() {
        let grants = [(USER.to_owned(), 0x1F01FF), (OTHER.to_owned(), 0x2)];

        assert!(!integrity_file_is_trusted(USER, Some(&grants), USER, None));
    }

    #[test]
    fn selector_creator_owner_grant_is_allowed() {
        let grants = [(CREATOR_OWNER.to_owned(), 0x2)];

        let trusted = selector_is_trusted(USER, Some(&grants), USER, None);

        assert!(trusted);
    }

    #[test]
    fn selector_rejects_foreign_write_and_control_masks() {
        const WRITE_DAC: u32 = 0x0004_0000;
        const WRITE_OWNER: u32 = 0x0008_0000;
        const GENERIC_WRITE: u32 = 0x4000_0000;
        const GENERIC_ALL: u32 = 0x1000_0000;

        for (label, mask) in [
            ("WIN_WRITE_MASK", WIN_WRITE_MASK),
            ("WIN_REPLACE_MASK", WIN_REPLACE_MASK),
            ("WRITE_DAC", WRITE_DAC),
            ("WRITE_OWNER", WRITE_OWNER),
            ("GENERIC_WRITE", GENERIC_WRITE),
            ("GENERIC_ALL", GENERIC_ALL),
        ] {
            let grants = [(USER.to_owned(), 0x1F01FF), (OTHER.to_owned(), mask)];
            assert!(
                !selector_is_trusted(USER, Some(&grants), USER, None),
                "selector_is_trusted must reject foreign {label} (0x{mask:08x})"
            );
        }
    }

    #[test]
    fn describe_untrusted_grants_reports_owner_null_dacl_and_foreign_masks() {
        let owner_msg = describe_untrusted_grants(OTHER, Some(&[]), USER, None);
        assert!(owner_msg.contains("untrusted owner"));
        assert!(owner_msg.contains(OTHER));

        let null_msg = describe_untrusted_grants(USER, None, USER, None);
        assert!(null_msg.contains("DACL is NULL or unavailable"));

        let grants = [(USER.to_owned(), 0x1F01FF), (OTHER.to_owned(), 0x0004_0000)];
        let grant_msg = describe_untrusted_grants(USER, Some(&grants), USER, None);
        assert!(grant_msg.contains(OTHER));
        assert!(grant_msg.contains("0x00040000"));
    }

    #[test]
    fn selector_token_default_administrator_owner_is_allowed() {
        let trusted = selector_is_trusted(ADMIN, Some(&[]), USER, Some(ADMIN));

        assert!(trusted);
    }

    #[test]
    fn selector_null_dacl_is_refused() {
        let trusted = selector_is_trusted(USER, None, USER, None);

        assert!(!trusted);
    }

    #[test]
    fn all_profiles_refuse_empty_current_user_sid() {
        for owner in [USER, SYSTEM, ADMIN, TRUSTED_INSTALLER] {
            assert!(!ancestor_is_trusted(owner, Some(&[]), ""));
            assert!(!parent_is_trusted(owner, Some(&[]), "", None));
            assert!(!integrity_file_is_trusted(owner, Some(&[]), "", None));
            assert!(!selector_is_trusted(owner, Some(&[]), "", None));
        }
        assert!(!parent_grants_are_trusted(Some(&[]), ""));
    }
    #[test]
    fn ancestor_foreign_owner_is_refused() {
        let trusted = ancestor_is_trusted(OTHER, Some(&[]), USER);

        assert!(!trusted);
    }

    #[test]
    fn ancestor_users_write_grant_is_refused() {
        let grants = [(USERS.to_owned(), 0x2)];

        let trusted = ancestor_is_trusted(USER, Some(&grants), USER);

        assert!(!trusted);
    }

    #[test]
    fn ancestor_authenticated_users_write_grant_is_refused() {
        let grants = [(AUTHENTICATED_USERS.to_owned(), 0x2)];

        let trusted = ancestor_is_trusted(USER, Some(&grants), USER);

        assert!(!trusted);
    }

    #[test]
    fn ancestor_owner_rights_and_administrator_grants_are_allowed() {
        let grants = [
            (OWNER_RIGHTS.to_owned(), 0x80000),
            (ADMIN.to_owned(), 0x40000000),
        ];

        let trusted = ancestor_is_trusted(USER, Some(&grants), USER);

        assert!(trusted);
    }

    #[test]
    fn parent_trustedinstaller_owner_without_token_owner_is_allowed() {
        let trusted = parent_is_trusted(TRUSTED_INSTALLER, Some(&[]), USER, None);

        assert!(trusted);
    }

    #[test]
    fn selector_user_system_and_administrator_grants_are_allowed() {
        let grants = [
            (USER.to_owned(), 0x10000000),
            (SYSTEM.to_owned(), 0x10000000),
            (ADMIN.to_owned(), 0x40000000),
        ];

        let trusted = selector_is_trusted(USER, Some(&grants), USER, None);

        assert!(trusted);
    }

    #[test]
    fn selector_administrator_owner_with_user_only_dacl_is_allowed() {
        let grants = [(USER.to_owned(), 0x1F01FF)];

        assert!(selector_is_trusted(ADMIN, Some(&grants), USER, None));
        assert_eq!(
            describe_untrusted_grants(ADMIN, Some(&grants), USER, None),
            "trusted"
        );
    }

    #[test]
    fn selector_system_owner_with_user_only_dacl_is_allowed() {
        let grants = [(USER.to_owned(), 0x1F01FF)];

        assert!(selector_is_trusted(SYSTEM, Some(&grants), USER, None));
    }

    #[test]
    fn selector_foreign_owner_is_refused() {
        let grants = [(USER.to_owned(), 0x1F01FF)];

        assert!(!selector_is_trusted(OTHER, Some(&grants), USER, None));
        let msg = describe_untrusted_grants(OTHER, Some(&grants), USER, None);
        assert!(msg.contains("untrusted owner"));
    }

    #[test]
    fn selector_administrator_owner_with_everyone_write_is_refused() {
        let grants = [(EVERYONE.to_owned(), 0x2)];

        assert!(!selector_is_trusted(ADMIN, Some(&grants), USER, None));
    }
}
