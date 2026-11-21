<!-- updated: 2026-10-30 -->

This policy describes data processing in Banking System, a game service for the Minecraft server “Demo Minecraft Server”. **Banking System does not provide real banking services.** Debris, accounts, payments and shares belong to the game economy.

## 1. General information

This policy covers website visitors, registered users and data obtained through connected game features, including ATMs.

**Banking System operator: Demo operator.**

Contact for personal data requests: **privacy@example.invalid**. The service is primarily hosted in the Czech Republic.

The service receives information from you, accounts you connect and activities within the service. Reading this policy does not itself constitute consent to every form of processing.

## 2. Data processed

| Category | Contents |
| --- | --- |
| Profile and sign-in | Profile identifier, login, bank username and display name, status, language and sign-in methods. When a password is configured, its Argon2id hash is stored in the database. |
| Connected accounts | Discord ID and username; when Minecraft is linked, the nickname, UUID and verification information. |
| Game finances | Virtual account numbers and names, balances, reservations, amounts, parties, descriptions, times and transaction identifiers; organizations, roles, payroll batches, payment requests and exchange activities. |
| Support | Ticket subject and category, messages, statuses, authors and timestamps; internal notes by authorized staff. |
| Technical information | Session information, User-Agent strings for the device list, request identifiers, timestamps, request outcomes and durations, and action audits. |

Minecraft ATMs also process observed nicknames, UUIDs, game coordinates and dimensions of nearby players, device states and evidence of game item movements. These observations can include players without a website account.

Network addresses are used by server-side request rate protection. Additional technical logs depend on the server infrastructure and proxy configuration.

Do not put passwords, secret API keys, identity documents or unnecessary personal information in payment descriptions or support messages.

When a news article is published, its text, the author's display name and dates are available to visitors of the public news section. Take this into account when preparing content.

## 3. Purposes of processing

Information is used to:

1. Create profiles, authenticate sign-ins and connect accounts.
2. Execute and record game operations, manage organizations, payroll, dividends, the exchange and payment requests.
3. Operate ATMs and reconcile actual debris movements.
4. Display history, deliver configured notifications and answer support requests.
5. Check permissions, prevent duplicate debits and investigate errors and abuse.

Withholding information necessary for a particular feature may make that feature unavailable.

## 4. Discord and Minecraft

Discord sign-in uses OAuth2 with the `identify` scope. The service receives the identifier and profile information used for sign-in and name display. This flow does not request the `email` scope or access to Discord conversations. A one-time OAuth code is exchanged with Discord for a token used to obtain the profile.

If Discord delivery is enabled, notifications may include a game event, amount, counterparty and link to the site. Check your notification preferences and direct-message availability.

Minecraft linking verifies the association between a profile and a game nickname and UUID. ATM operations involve game messages and observations of nearby players. The identity verification method depends on the server settings.

Discord and the Minecraft server operator also have their own terms and privacy notices for their processing activities.

## 5. Recipients and access

Parties to payments and organization members receive information needed for the relevant features within their permissions. Authorized administrators and support staff have access within their granted permissions.

Confirmed integrations are described above. Opening external links or loading an external news cover establishes a network connection to the relevant third-party resource.

Banking System runs on the operator’s own server in the Czech Republic. Cloudflare provides DNS and web traffic proxying (https://www.cloudflare.com/privacypolicy/). Discord is used for Discord sign-in (https://discord.com/privacy). Regular backups of user data are not currently configured.

## 6. Retention

Technical logs (including API errors, IP addresses and login attempts) are retained for up to **30 calendar days**. Virtual financial transaction history and administrative audit logs are retained for **365 calendar days** from the event. Closed support tickets are retained for **30 calendar days** after closure. After an approved account deletion request, personal data no longer needed is deleted or anonymized without undue delay. Transaction history may remain after account deletion until the 365-day period expires where a lawful basis exists; identifying information is minimized. Longer retention is allowed only where legally justified. Approved notification and binding periods are detailed below. Regular backups are not currently created.

Default settings allow a session to last up to 30 days of inactivity, with an absolute limit of 90 days from sign-in; OAuth state expires after 10 minutes. Deployment settings may differ. **Access expiration does not mean database records are automatically deleted.**

The financial journal and parts of the audit trail resist ordinary editing and deletion. This technical property does not replace the operator's obligations to determine lawful retention and handle user requests.

## 7. Cookies and browser storage

| Mechanism | Purpose |
| --- | --- |
| `horizon_session` | Maintaining sign-in; expiration depends on session settings. |
| `horizon_oauth_state` | Temporarily binding the request to the return from Discord. |
| localStorage | Language, theme, concealed amounts, and saved stock issuance drafts and plans. |
| sessionStorage | Information for continuing transfer confirmation and returning to a payment page in the current tab. |

You can clear local data using browser controls. Blocking cookies may prevent sign-in; clearing storage may remove settings and unfinished drafts, but does not delete server-side transaction history.

The Banking System frontend code does not include external analytics or advertising SDKs.

## 8. Security

The service includes reasonable safeguards: server-side permission checks, password and session token hashing, request rate limits, audits and verification of financial operation outcomes. Authentication cookies use `HttpOnly` and `SameSite=Lax`, with `Secure` in production mode; production settings require an HTTPS service address.

Protection also depends on the operator's configuration and maintenance of the infrastructure. Absolute information system security cannot be guaranteed. This does not remove the operator's liability or incident-related duties under applicable law.

## 9. User rights and requests

Depending on applicable law, you may request information about processing and access to your data, correction, deletion, restriction, object to processing or request portability. Where processing relies on consent, you may withdraw it through the established procedure. Where entitled, you may contact the competent supervisory authority.

Send requests using the contacts in section 12. The operator may need to verify that the request relates to your account. Do not send passwords or secret keys. Closing a virtual account or unlinking Discord/Minecraft does not itself erase all related history.

Requests, including account deletion requests, may be submitted through Banking System support or to **privacy@example.invalid**, including without signing in. Reasonable identity verification may be required to protect personal data. GDPR requests are generally answered within one month, subject to extensions allowed by law. The Czech supervisory authority is Úřad pro ochranu osobních údajů (https://uoou.gov.cz/).

## 10. Children and young users

Registration requires confirmation that the user is at least 15. No exact date of birth or documents are requested for this purpose; confirmation is based on the user’s statement.

Banking System is intended for users **aged 15 and older**. Users under 15 are not permitted to register or use the service. If an underage account is identified, the operator may restrict access and take lawful steps to delete associated personal data. Mandatory identity-document age verification is not planned. Use the contacts in section 12 for questions about a child's data.

## 11. Policy changes

The current text is published on this page, with the update date above the document. Policy changes do not automatically constitute consent to new processing purposes.

Material changes to this Policy are announced through Banking System’s built-in **news section**. The current version is published at `/privacy` with its update date. If separate consent is legally required for new processing, it will be requested beforehand; continued use alone does not replace required consent.

## 12. Contacts

**Operator: Demo operator.**

**Email for personal data requests: privacy@example.invalid.**

Registered users can open the dashboard support chat for ordinary technical questions. This chat does not replace a public operator contact: data requests must also be accessible without signing in.

### Approved periods and request handling

Read notifications are deleted 30 days after reading; unread notifications 90 days after creation. Unlinking immediately removes binding identifiers and unnecessary historical copies; essential financial evidence is handled separately.

Backups currently occur during version upgrades and are retained for **7 days**. Regular backups are not enabled. Automated cleanup requires separate operator activation after a successful backup restore check; until then the cleanup schedule must not be described as active. The prepared maintenance job creates a verified backup before cleanup using the same seven-day retention. After restoring an older backup, the operator must reapply subsequent erasures and restrictions before reopening access.

The financial journal is the source of balances: after 365 days free text and additional identifying information are minimized, but structural pseudonymous links, amounts and evidence required for balances, asset ownership and replay protection are not automatically destroyed. Continued retention requires a separately established applicable legal basis and a review of necessity. These are not anonymous data, and this is not a promise to delete all financial history after 365 days.

Authorized staff record requests received through support or email. Exports require staff review before disclosure, excluding authentication secrets and third-party information. Erasure may await an unfinished operation, dispute review, asset disposal or ATM journal cleanup. The result is communicated through the original contact channel when available. Signing out clears account drafts on the current device; the operator cannot remotely remove copies in an offline browser or Discord messages already delivered.
