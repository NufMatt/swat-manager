# config-testing.py
GUILD_ID = 958271853158350850  # Changed for testing server
CNR_ID = 738534756538384499

# -----------------------
# CHANNEL IDS
# -----------------------
TRAINEE_NOTES_CHANNEL = 1150815370052108431  # Changed
CADET_NOTES_CHANNEL   = 1150815012739362867  # Changed
TRAINEE_CHAT_CHANNEL  = 1033433893199679599  # Changed
SWAT_CHAT_CHANNEL     = 1150814313444028537  # Changed
TARGET_CHANNEL_ID     = 1334972899416739982  # Changed
REQUESTS_CHANNEL_ID   = 1334973175892545578  # Changed
STATUS_CHANNEL_ID     = 1322097975324971068  # Changed
TICKET_CHANNEL_ID     = 1303104817228677150  # Changed
APPLY_CHANNEL_ID      = 1361636899361521776
ACTIVITY_CHANNEL_ID   = 1361636237840089099
VERIFY_CHANNEL_ID    = 1370260376276697140

# -----------------------
# ROLE IDS
# -----------------------
TRAINEE_ROLE          = 1033432392758722682  # Changed
CADET_ROLE            = 962226985222959145  # Changed
SWAT_ROLE_ID          = 958274314036195359  # Changed
OFFICER_ROLE_ID       = 958272804011245618  # Changed
RECRUITER_ID          = 1033530640014004254  # Changed
LEADERSHIP_ID         = 1253670680545722461  # Changed
EU_ROLE_ID            = 1080548138848370809  # Changed
NA_ROLE_ID            = 1080547954785538048  # Changed
SEA_ROLE_ID           = 1145007712606883882  # Changed
LEAD_BOT_DEVELOPER_ID = 1160306226517786794  # Changed
MENTOR_ROLE_ID        = 1303048285040410644  # Changed
GUEST_ROLE            = 974835615424192552   # UNCHANGED
VERIFIED_ROLE         = 1216355281139798169  # Changed
BLACKLISTED_ROLE_ID   = 1363175970172965156
TIMEOUT_ROLE_ID       = 1363175797304590508

# -----------------------
# EMOJIS
# -----------------------
PLUS_ONE_EMOJI            = "<:plus_one:1334498534187208714>"  # Changed
MINUS_ONE_EMOJI           = "<:minus_one:1334498485390544989>"  # Changed
LEADERSHIP_EMOJI          = "<:leadership:1337037982930309224>"  # Changed
RECRUITER_EMOJI           = "<:recruiter:1337037961421656086>"  # Changed
LEAD_BOT_DEVELOPER_EMOJI  = "<:leaddeveloper:1337040174022131772>"  # Changed
INTEGRATIONS_MANAGER      = "<:integarationsmanager:1337040186986594367>"  # Changed
SWAT_LOGO_EMOJI           = "<:swat_logo:1356719341759889530>"
TRAINEE_EMOJI             = "<:trainee:1356720724567654670>"
CADET_EMOJI               = "<:cadet:1356720703784620183>"
MENTOR_EMOJI              = "<:mentor:1356720745073610984>"

# -----------------------
# TICKETS
# -----------------------
TOKEN_FILE = "token.txt"  # Changed to the testing token

# -----------------------
# PLAYER LIST SETTINGS (UNCHANGED)
# -----------------------
USE_LOCAL_JSON = False
LOCAL_JSON_FILE = "json-formatting.json"
CHECK_INTERVAL = 30         # in seconds
CACHE_UPDATE_INTERVAL = 300  # in seconds
SWAT_WEBSITE_URL = "https://cnrswat.com"
SWAT_WEBSITE_TOKEN_FILE = "website-api-key.txt"
SEND_API_DATA = True

API_URLS = {
    #"SEA": "https://api.gtacnr.net/cnr/players?serverId=SEA",
    #"NA3": "https://api.gtacnr.net/cnr/players?serverId=US3",
    "NA2": "https://api.gtacnr.net/cnr/players?serverId=US2",
    "NA1": "https://api.gtacnr.net/cnr/players?serverId=US1",
    "EU2": "https://api.gtacnr.net/cnr/players?serverId=EU2",
    "EU1": "https://api.gtacnr.net/cnr/players?serverId=EU1",
}

API_URLS_FIVEM = {
    "EU1": "https://109.61.89.213:30120/info.json",
    "EU2": "https://109.61.89.213:30121/info.json",
    "NA1": "https://212.102.58.130:30120/info.json",
    "NA2": "https://212.102.58.130:30121/info.json",
    #"NA3": "https://45.88.228.198:30122/info.json",
    #"SEA": "https://138.199.25.49:30120/info.json,"
}

RANK_HIERARCHY = [
    "Mentor", "Chief", "Deputy Chief", "Commander",
    "Captain", "Lieutenant", "Seargent", "Corporal",
    "Officer", "Cadet", "Trainee", None
]
ROLE_TO_RANK = {
    1303048285040410644: "Mentor",
    958272560905195521: "Chief",
    958272662080225290: "Deputy Chief",
    958272697291407360: "Commander",
    958272723975553085: "Captain",
    958272744800260126: "Lieutenant",
    958272773904543775: "Seargent",
    966118860128411681: "Corporal",
    958272804011245618: "Officer",
    962226985222959145: "Cadet",
    1033432392758722682: "Trainee",
}

# -----------------------
# Verification Bot
# -----------------------
CHECK_CNR_VERIFIED_ROLE = 871086120698523668
