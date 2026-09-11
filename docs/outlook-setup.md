# Outlook.com / Hotmail / Microsoft 365 setup

Only needed for `"source": "graph"`. If you use Gmail or any IMAP provider, ignore this
file — an app password is all you need.

This takes about five minutes and is free. Three of the steps fail in confusing ways if
you miss them, so they are called out below.

1. Sign in at [entra.microsoft.com](https://entra.microsoft.com) **with the mailbox you
   want to scan**. A personal Microsoft account works; Azure creates a directory for you.
2. **Applications → App registrations → New registration.**
3. Name it anything. For **Supported account types** pick **Personal Microsoft accounts
   only** if you are scanning Outlook.com/Hotmail.
4. Leave Redirect URI blank — device-code sign-in does not use one. **Register.**
5. Copy the **Application (client) ID** into `config.json` as `client_id`, or set
   `GRAPH_CLIENT_ID` in your environment.
6. **Authentication → Allow public client flows → Yes → Save.**
7. **API permissions → Add a permission → Microsoft Graph → Delegated permissions →
   `Mail.Read`.** Add `User.Read` too. No admin consent needed.

8. **Install with the `graph` extra.** Outlook talks to Microsoft Graph over REST,
   which needs two packages the core does not use — `msal` and `requests`. The plain
   install does not include them:

   ```bash
   pip install -e ".[graph]"      # or, without installing the project: pip install msal requests
   ```

Then run it:

```bash
inbox-job-tracker run
```

If that reports `command not found`, the package is not installed or the virtualenv is
not active. Either activate it, or run the module directly — same program, no install
needed:

```bash
python -m inboxjobtracker.cli run
```

First run prints a device code; sign in with it once and the token is cached.

## The three that bite

**`AADSTS700016: Application not found in the directory`**
The authority does not match the account type. Scanning a personal mailbox means
`"authority": "https://login.microsoftonline.com/consumers"` *and* a registration that
accepts personal accounts. For a work mailbox, use your tenant ID instead.

**Cannot change Supported account types — `requestedAccessTokenVersion is invalid`**
Personal accounts require v2 tokens, and a single-tenant registration starts on v1. Edit
the **Manifest** and change both together in one save:

```json
"signInAudience": "AzureADandPersonalMicrosoftAccount",
"api": { "requestedAccessTokenVersion": 2 }
```

Either alone is rejected. It is often quicker to delete the registration and create a new
one, choosing the right account type at step 3.

**`AADSTS7000218: request body must contain client_assertion or client_secret`**
Step 6 was missed. Turn on **Allow public client flows**.

**Signed in, but every folder returns 401**
The token is for a directory guest (`...#EXT#@...onmicrosoft.com`) which has no mailbox.
Use `/consumers` as the authority so you sign in as the personal account itself.

**Corporate tenants** often block device-code flow via Conditional Access
(`AADSTS50199`/`50076`). There is no workaround from this side; use IMAP if your
employer allows it.
