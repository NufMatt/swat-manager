# config-testing.py
GUILD_ID = 1300519755622383689  # Changed for testing server
CNR_ID = 738534756538384499

# -----------------------
# CHANNEL IDS
# -----------------------
TRAINEE_NOTES_CHANNEL = 1334493226148691989  # Changed
CADET_NOTES_CHANNEL   = 1334493243018182699  # Changed
TRAINEE_CHAT_CHANNEL  = 1334534670330761389  # Changed
SWAT_CHAT_CHANNEL     = 1324733745919692800  # Changed
TARGET_CHANNEL_ID     = 1334474489236557896  # Changed
REQUESTS_CHANNEL_ID   = 1334474601668804638  # Changed
STATUS_CHANNEL_ID     = 1320463232128913551  # Changed
TICKET_CHANNEL_ID     = 1334880226089500732  # Changed
APPLY_CHANNEL_ID      = 1350481022734696468
ACTIVITY_CHANNEL_ID   = 1350858506344988732

# -----------------------
# ROLE IDS
# -----------------------
TRAINEE_ROLE          = 1321853549273157642  # Changed
CADET_ROLE            = 1321853586384093235  # Changed
SWAT_ROLE_ID          = 1321163290948145212  # Changed
OFFICER_ROLE_ID       = 1334844188470022144  # Changed
RECRUITER_ID          = 1334600500448067707  # Changed
LEADERSHIP_ID         = 1335590246074810459  # Changed
EU_ROLE_ID            = 1334943073519538217  # Changed
NA_ROLE_ID            = 1334942947703132290  # Changed
SEA_ROLE_ID           = 1334943169485475840  # Changed
LEAD_BOT_DEVELOPER_ID = 1337035136088412160  # Changed
MENTOR_ROLE_ID        = 1320457877508460565  # Changed
GUEST_ROLE            = 974835615424192552   # UNCHANGED
VERIFIED_ROLE         = 1329536911609565246  # Changed
BLACKLISTED_ROLE_ID   = 1356368351412359188
TIMEOUT_ROLE_ID       = 1356368442072240138

# -----------------------
# EMOJIS
# -----------------------
PLUS_ONE_EMOJI            = "<:plus_one:1335346081797636269>"  # Changed
MINUS_ONE_EMOJI           = "<:minus_one:1335346048125632567>"  # Changed
LEADERSHIP_EMOJI          = "<:leadership:1337038296358064169>"  # Changed
RECRUITER_EMOJI           = "<:recruiter:1337038280675426345>"  # Changed
LEAD_BOT_DEVELOPER_EMOJI  = "<:leaddeveloper:1337040075241951296>"  # Changed
INTEGRATIONS_MANAGER      = "<:integarationsmanager:1337040123988017304>"  # Changed
SWAT_LOGO_EMOJI           = "<:swat_logo:1356718858098049327>"
TRAINEE_EMOJI             = "<:trainee:1356721158585844074>"
CADET_EMOJI               = "<:cadet:1356721137828106310>"
MENTOR_EMOJI              = "<:mentor:1356721177028067338>"

# -----------------------
# TICKETS
# -----------------------
TOKEN_FILE = "token-test.txt"  # Changed to the testing token

# -----------------------
# PLAYER LIST SETTINGS (UNCHANGED)
# -----------------------
USE_LOCAL_JSON = False
LOCAL_JSON_FILE = "json-formatting.json"
CHECK_INTERVAL = 30         # in seconds
CACHE_UPDATE_INTERVAL = 300  # in seconds
SWAT_WEBSITE_URL = "https://cnrswat.com"
SWAT_WEBSITE_TOKEN_FILE = "website-api-key.txt"

API_URLS = {
    "SEA": "https://api.gtacnr.net/cnr/players?serverId=SEA",
    "NA3": "https://api.gtacnr.net/cnr/players?serverId=US3",
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
    "NA3": "https://45.88.228.198:30122/info.json",
    "SEA": "https://51.79.231.52:30130/info.json",
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

EMBEDS_FILE = "embeds.json"

# -----------------------
# Verification Bot
# -----------------------
CHECK_CNR_VERIFIED_ROLE = 871086120698523668
