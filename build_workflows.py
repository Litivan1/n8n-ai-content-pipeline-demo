"""
Generates the three n8n workflow JSON files in workflows/ programmatically,
so node ids/positions/connections stay internally consistent and the files
are valid to import into n8n directly.

Run once: python build_workflows.py
"""
import json
import uuid


def nid():
    return str(uuid.uuid4())


def node(name, ntype, params=None, pos=(0, 0), type_version=1, extra=None):
    n = {
        "parameters": params or {},
        "id": nid(),
        "name": name,
        "type": ntype,
        "typeVersion": type_version,
        "position": list(pos),
    }
    if extra:
        n.update(extra)
    return n


def connect(*chain_or_branches):
    """
    Build a connections dict from either a simple linear chain of node
    names, or a dict describing branches: {"From": ["To1", "To2"]}.
    """
    connections = {}
    if isinstance(chain_or_branches[0], dict):
        for src, targets in chain_or_branches[0].items():
            connections[src] = {
                "main": [[{"node": t, "type": "main", "index": 0}] for t in targets]
            }
        return connections

    chain = chain_or_branches
    for a, b in zip(chain, chain[1:]):
        connections[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
    return connections


def workflow(name, nodes, connections):
    return {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "versionId": nid(),
        "meta": {"instanceId": "demo"},
        "tags": [],
    }


# ---------------------------------------------------------------------------
# Workflow 1: intake + AI generation
# ---------------------------------------------------------------------------

n1 = [
    node("Telegram Webhook", "n8n-nodes-base.webhook",
         {"httpMethod": "POST", "path": "content-intake", "responseMode": "onReceived"},
         (0, 0), 2),
    node("Has media?", "n8n-nodes-base.if",
         {"conditions": {"boolean": [{"value1": "={{ $json.message.photo || $json.message.video }}", "value2": True}]}},
         (250, 0)),
    node("Save brief text", "n8n-nodes-base.set",
         {"values": {"string": [{"name": "brief_text", "value": "={{ $json.message.text }}"}]}},
         (500, 100)),
    node("Vision: describe media (Groq)", "n8n-nodes-base.httpRequest",
         {
             "method": "POST",
             "url": "https://api.groq.com/openai/v1/chat/completions",
             "sendHeaders": True,
             "headerParameters": {"parameters": [
                 {"name": "Authorization", "value": "=Bearer {{ $env.GROQ_API_KEY }}"}
             ]},
             "sendBody": True,
             "specifyBody": "json",
             "jsonBody": "={{ JSON.stringify({model: \"meta-llama/llama-4-scout-17b-16e-instruct\", max_tokens: 300, messages: [{role: \"user\", content: [{type: \"image_url\", image_url: {url: $json.media_url}}, {type: \"text\", text: \"Describe only what is literally visible in this image: subject, setting, notable objects, lighting. Do not invent details you cannot see.\"}]}]}) }}",
         },
         (500, -100)),
    node("Summarize facts (Groq)", "n8n-nodes-base.httpRequest",
         {
             "method": "POST",
             "url": "https://api.groq.com/openai/v1/chat/completions",
             "sendHeaders": True,
             "headerParameters": {"parameters": [
                 {"name": "Authorization", "value": "=Bearer {{ $env.GROQ_API_KEY }}"}
             ]},
             "sendBody": True,
             "specifyBody": "json",
             "jsonBody": "={{ JSON.stringify({model: \"llama-3.3-70b-versatile\", messages: [{role: \"user\", content: \"Extract only the factual highlights from this brief and media description, 3-5 plain sentences, no invented details:\\n\\nBRIEF: \" + $json.brief_text + \"\\n\\nMEDIA: \" + $json.media_description}]}) }}",
         },
         (750, 0)),
    node("Voyage: embed summary", "n8n-nodes-base.httpRequest",
         {
             "method": "POST",
             "url": "https://api.voyageai.com/v1/embeddings",
             "sendHeaders": True,
             "headerParameters": {"parameters": [
                 {"name": "Authorization", "value": "=Bearer {{ $env.VOYAGE_API_KEY }}"}
             ]},
             "sendBody": True,
             "specifyBody": "json",
             "jsonBody": "={{ JSON.stringify({model: \"voyage-3\", input: [$json.summary]}) }}",
         },
         (1000, 0)),
    node("Find similar past posts (RAG)", "n8n-nodes-base.httpRequest",
         {
             "method": "POST",
             "url": "={{ $env.SUPABASE_URL }}/rest/v1/rpc/match_posts",
             "sendHeaders": True,
             "headerParameters": {"parameters": [
                 {"name": "apikey", "value": "={{ $env.SUPABASE_KEY }}"}
             ]},
             "sendBody": True,
             "specifyBody": "json",
             "jsonBody": "={{ JSON.stringify({query_embedding: $json.embedding, match_count: 3}) }}",
         },
         (1250, 0)),
    node("Fan out per platform", "n8n-nodes-base.code",
         {
             "jsCode": (
                 "const platforms = [\n"
                 "  {platform: 'telegram', tone: 'casual, 150-200 words, 1-2 emoji, end with a question'},\n"
                 "  {platform: 'vk', tone: 'informative, 250-350 words, 4-6 hashtags at the end'},\n"
                 "  {platform: 'instagram', tone: 'hook in the first line, 100-150 words, 10-12 hashtags'},\n"
                 "];\n"
                 "return platforms.map(p => ({ json: { ...$input.item.json, ...p } }));"
             )
         },
         (1500, 0)),
    node("Generate post (Groq, style-matched)", "n8n-nodes-base.httpRequest",
         {
             "method": "POST",
             "url": "https://api.groq.com/openai/v1/chat/completions",
             "sendHeaders": True,
             "headerParameters": {"parameters": [
                 {"name": "Authorization", "value": "=Bearer {{ $env.GROQ_API_KEY }}"}
             ]},
             "sendBody": True,
             "specifyBody": "json",
             "jsonBody": "={{ JSON.stringify({model: \"llama-3.3-70b-versatile\", messages: [{role: \"user\", content: \"Write a social post for \" + $json.platform + \". Tone: \" + $json.tone + \". Use these facts only: \" + $json.summary + \". Match the style of these past examples: \" + JSON.stringify($json.style_examples)}]}) }}",
             "options": {"batching": {"batch": {"batchSize": 1, "batchInterval": 9000}}},
         },
         (1750, 0)),
    node("Send draft for approval", "n8n-nodes-base.httpRequest",
         {
             "method": "POST",
             "url": "https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage",
             "sendBody": True,
             "specifyBody": "json",
             "jsonBody": "={{ JSON.stringify({chat_id: $env.ADMIN_CHAT_ID, text: $json.generated_post, reply_markup: {inline_keyboard: [[{text: 'Approve', callback_data: 'approve:' + $json.post_id}, {text: 'Edit', callback_data: 'edit:' + $json.post_id}, {text: 'Reject', callback_data: 'reject:' + $json.post_id}]]}}) }}",
         },
         (2000, 0)),
]

c1 = connect([
    ("Telegram Webhook", "Has media?"),
    ("Has media?", "Vision: describe media (Groq)"),
    ("Has media?", "Save brief text"),
    ("Vision: describe media (Groq)", "Summarize facts (Groq)"),
    ("Save brief text", "Summarize facts (Groq)"),
    ("Summarize facts (Groq)", "Voyage: embed summary"),
    ("Voyage: embed summary", "Find similar past posts (RAG)"),
    ("Find similar past posts (RAG)", "Fan out per platform"),
    ("Fan out per platform", "Generate post (Groq, style-matched)"),
    ("Generate post (Groq, style-matched)", "Send draft for approval"),
])
# fix: "Has media?" needs two branches — rebuild its connection manually
c1["Has media?"] = {
    "main": [
        [{"node": "Vision: describe media (Groq)", "type": "main", "index": 0}],
        [{"node": "Save brief text", "type": "main", "index": 0}],
    ]
}

wf1 = workflow("1 - Intake and generation", n1, c1)

# ---------------------------------------------------------------------------
# Workflow 2: approval
# ---------------------------------------------------------------------------

n2 = [
    node("Approval Webhook", "n8n-nodes-base.webhook",
         {"httpMethod": "POST", "path": "content-approval", "responseMode": "onReceived"},
         (0, 0), 2),
    node("Parse callback_data", "n8n-nodes-base.set",
         {"values": {"string": [
             {"name": "action", "value": "={{ $json.callback_query.data.split(':')[0] }}"},
             {"name": "post_id", "value": "={{ $json.callback_query.data.split(':')[1] }}"},
         ]}},
         (250, 0)),
    node("action == approve?", "n8n-nodes-base.if",
         {"conditions": {"string": [{"value1": "={{ $json.action }}", "value2": "approve"}]}},
         (500, 0)),
    node("Mark approved (PATCH)", "n8n-nodes-base.httpRequest",
         {"method": "PATCH", "url": "={{ $env.SUPABASE_URL }}/rest/v1/posts?id=eq.{{ $json.post_id }}",
          "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify({status: 'approved'}) }}"},
         (750, -100)),
    node("Mark rejected (PATCH)", "n8n-nodes-base.httpRequest",
         {"method": "PATCH", "url": "={{ $env.SUPABASE_URL }}/rest/v1/posts?id=eq.{{ $json.post_id }}",
          "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify({status: 'rejected'}) }}"},
         (750, 100)),
]

c2 = connect([
    ("Approval Webhook", "Parse callback_data"),
    ("Parse callback_data", "action == approve?"),
])
c2["action == approve?"] = {
    "main": [
        [{"node": "Mark approved (PATCH)", "type": "main", "index": 0}],
        [{"node": "Mark rejected (PATCH)", "type": "main", "index": 0}],
    ]
}

wf2 = workflow("2 - Approval", n2, c2)

# ---------------------------------------------------------------------------
# Workflow 3: scheduled publish
# ---------------------------------------------------------------------------

n3 = [
    node("Every hour", "n8n-nodes-base.scheduleTrigger",
         {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}},
         (0, 0)),
    node("Fetch approved posts", "n8n-nodes-base.httpRequest",
         {"method": "GET", "url": "={{ $env.SUPABASE_URL }}/rest/v1/posts?status=eq.approved",
          "sendHeaders": True,
          "headerParameters": {"parameters": [{"name": "apikey", "value": "={{ $env.SUPABASE_KEY }}"}]}},
         (250, 0)),
    node("platform == telegram?", "n8n-nodes-base.if",
         {"conditions": {"string": [{"value1": "={{ $json.platform }}", "value2": "telegram"}]}},
         (500, 0)),
    node("Publish: Telegram", "n8n-nodes-base.httpRequest",
         {"method": "POST", "url": "https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage",
          "sendBody": True, "specifyBody": "json",
          "jsonBody": "={{ JSON.stringify({chat_id: $env.CHANNEL_ID, text: $json.generated_post}) }}"},
         (750, -150)),
    node("Publish: VK", "n8n-nodes-base.httpRequest",
         {"method": "POST", "url": "https://api.vk.com/method/wall.post",
          "sendBody": True, "specifyBody": "json",
          "jsonBody": "={{ JSON.stringify({message: $json.generated_post, access_token: $env.VK_TOKEN}) }}"},
         (750, 0)),
    node("Check daily limit: Instagram", "n8n-nodes-base.httpRequest",
         {"method": "GET", "url": "={{ $env.SUPABASE_URL }}/rest/v1/posts?platform=eq.instagram&published_at=gte.today"},
         (750, 150)),
    node("Publish: Instagram", "n8n-nodes-base.httpRequest",
         {"method": "POST", "url": "https://graph.facebook.com/v19.0/{{ $env.IG_USER_ID }}/media_publish",
          "sendBody": True, "specifyBody": "json",
          "jsonBody": "={{ JSON.stringify({creation_id: $json.creation_id, access_token: $env.IG_TOKEN}) }}"},
         (1000, 150)),
    node("Mark published (PATCH)", "n8n-nodes-base.httpRequest",
         {"method": "PATCH", "url": "={{ $env.SUPABASE_URL }}/rest/v1/posts?id=eq.{{ $json.post_id }}",
          "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify({status: 'published'}) }}"},
         (1250, 0)),
    node("Compute embedding for published post", "n8n-nodes-base.httpRequest",
         {"method": "POST", "url": "https://api.voyageai.com/v1/embeddings",
          "sendBody": True, "specifyBody": "json",
          "jsonBody": "={{ JSON.stringify({model: 'voyage-3', input: [$json.generated_post]}) }}"},
         (1500, 0)),
    node("Store embedding (Supabase)", "n8n-nodes-base.httpRequest",
         {"method": "PATCH", "url": "={{ $env.SUPABASE_URL }}/rest/v1/posts?id=eq.{{ $json.post_id }}",
          "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify({embedding: $json.embedding}) }}"},
         (1750, 0)),
]

c3 = connect([
    ("Every hour", "Fetch approved posts"),
    ("Fetch approved posts", "platform == telegram?"),
])
c3["platform == telegram?"] = {
    "main": [
        [{"node": "Publish: Telegram", "type": "main", "index": 0}],
        [{"node": "Publish: VK", "type": "main", "index": 0}, {"node": "Check daily limit: Instagram", "type": "main", "index": 0}],
    ]
}
c3["Publish: Telegram"] = {"main": [[{"node": "Mark published (PATCH)", "type": "main", "index": 0}]]}
c3["Publish: VK"] = {"main": [[{"node": "Mark published (PATCH)", "type": "main", "index": 0}]]}
c3["Check daily limit: Instagram"] = {"main": [[{"node": "Publish: Instagram", "type": "main", "index": 0}]]}
c3["Publish: Instagram"] = {"main": [[{"node": "Mark published (PATCH)", "type": "main", "index": 0}]]}
c3["Mark published (PATCH)"] = {"main": [[{"node": "Compute embedding for published post", "type": "main", "index": 0}]]}
c3["Compute embedding for published post"] = {"main": [[{"node": "Store embedding (Supabase)", "type": "main", "index": 0}]]}

wf3 = workflow("3 - Scheduled publish", n3, c3)

for fname, wf in [
    ("workflows/1-intake-and-generation.json", wf1),
    ("workflows/2-approval.json", wf2),
    ("workflows/3-scheduled-publish.json", wf3),
]:
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False, indent=2)
    print("wrote", fname)
