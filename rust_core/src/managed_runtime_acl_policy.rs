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

/// Trust the selector's parent directory; owner may also be the verified token default owner.
/// A NULL, unavailable, or unparsed DACL must be passed as `None` and is rejected.
pub fn parent_is_trusted(
    owner: &str,
    grants: Option<&[(String, u32)]>,
    user_sid: &str,
    token_owner_sid: Option<&str>,
) -> bool {
    if !valid_owner(owner, user_sid, token_owner_sid) {
        return false;
    }
    let Some(grants) = grants else {
        return false;
    };
    grants.iter().all(|(sid, mask)| {
        sid == user_sid || is_trusted_grant_sid(sid) || mask & WIN_WRITE_MASK == 0
    })
}

/// Trust the selector file; only the user, SYSTEM, or Administrators may have an allow grant.
/// A NULL, unavailable, or unparsed DACL must be passed as `None` and is rejected.
pub fn selector_is_trusted(
    owner: &str,
    grants: Option<&[(String, u32)]>,
    user_sid: &str,
    token_owner_sid: Option<&str>,
) -> bool {
    if !valid_owner(owner, user_sid, token_owner_sid) {
        return false;
    }
    let Some(grants) = grants else {
        return false;
    };
    grants
        .iter()
        .all(|(sid, _)| sid == user_sid || sid == SID_SYSTEM || sid == SID_ADMINISTRATORS)
}

fn valid_owner(owner: &str, user_sid: &str, token_owner_sid: Option<&str>) -> bool {
    !owner.is_empty()
        && !user_sid.is_empty()
        && (owner == user_sid
            || token_owner_sid
                .is_some_and(|token_owner| !token_owner.is_empty() && owner == token_owner))
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
    use super::{ancestor_is_trusted, parent_is_trusted, selector_is_trusted};

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
    fn selector_foreign_read_grant_is_refused() {
        let grants = [(OTHER.to_owned(), 0x1)];

        let trusted = selector_is_trusted(USER, Some(&grants), USER, None);

        assert!(!trusted);
    }

    #[test]
    fn selector_creator_owner_grant_is_refused() {
        let grants = [(CREATOR_OWNER.to_owned(), 0x2)];

        let trusted = selector_is_trusted(USER, Some(&grants), USER, None);

        assert!(!trusted);
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
        let ancestor = ancestor_is_trusted(USER, Some(&[]), "");
        let parent = parent_is_trusted(USER, Some(&[]), "", None);
        let selector = selector_is_trusted(USER, Some(&[]), "", None);

        assert!(!ancestor);
        assert!(!parent);
        assert!(!selector);
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
    fn parent_trustedinstaller_owner_without_token_owner_is_refused() {
        let trusted = parent_is_trusted(TRUSTED_INSTALLER, Some(&[]), USER, None);

        assert!(!trusted);
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
}
