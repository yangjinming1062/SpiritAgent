import json
import logging
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx
from modules.media import SpeechStyle

from ..base import AudioChunk, ProviderConfig, TTSProvider, TTSResult, VoiceDesignResult, pick_catalog_voice
from ..http import get_http
from ..speech_style import speech_style_matches, styled_speech_text
from ._errors import extract_minimax_audio, raise_for_minimax_response, raise_for_minimax_stream_event

logger = logging.getLogger(__name__)

# 流式 SSE 事件间隙可能超过共享客户端的默认 read 超时（按整段请求时长设定），流式请求单独放宽。
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


class MiniMaxTTSProvider(TTSProvider):
    """通过 MiniMax 同步 POST /v1/t2a_v2 提供 TTS（{model,text,voice_setting:{voice_id,speed,vol},audio_setting:{format,sample_rate}}，data.audio 为 hex 编码音频，本模块解码回字节）；流式走同端点 stream=true SSE（事件 data.audio hex + data.status 1/2，末事件默认重复携带聚合音频需显式排除）；异步长文本 /v1/t2a_async_v2 未封装，chat 回复足够短，落在 10000 字符同步上限内。"""

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "speech-2.8-hd"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"tts": 8_000}
    SUPPORTS_SYNTH_STREAM = True
    VOICE_DESIGN_GUIDE = """\
用一段文字描述你想要的音色，描述越具体效果越好。建议涵盖：
• 性别与年龄：如"沉稳可靠的中年男性"、"专业播音腔的中年女性"
• 音色质感：如"低沉富有磁性"、"清亮柔和"
• 情绪语气：如"温柔自信"、"慵懒俏皮"
• 语速节奏：如"语速时快时慢"、"缓慢沉稳"
preview_text 为试听文本——设计完成后会用它合成一段示例音频供你试听。\
"""
    # id/label 与 MiniMax 官方系统音色一致（POST /v1/get_voice voice_type=all 返回 303 条；本目录 81 条均经 t2a_v2 "你" 实测通过 base_resp.status_code=0）。
    # 多语种 (pt/es/ru/it/ar/tr/de/fr/uk/vi/ja/ko/id) 共 222 条未收录（项目侧仅支持 zh/en 系统语言；如需扩展，按同样规则追加 + 探活）。
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {
            "id": "female-chengshu",
            "label": "成熟女性音色",
            "gender": "female",
            "language": "zh",
            "tags": ["知性", "温柔", "成熟", "女", "稳重"],
            "description": "温柔知性的成熟女声。",
        },
        {
            "id": "female-shaonv",
            "label": "少女音色",
            "gender": "female",
            "language": "zh",
            "tags": ["少女", "温柔", "甜", "活泼", "女"],
            "description": "清甜的少女音，活泼温柔。",
        },
        {
            "id": "female-yujie",
            "label": "御姐音色",
            "gender": "female",
            "language": "zh",
            "tags": ["御姐", "清冷", "成熟", "沉稳", "女"],
            "description": "清冷成熟的御姐音。",
        },
        {
            "id": "qiaopi_mengmei",
            "label": "俏皮萌妹",
            "gender": "female",
            "language": "zh",
            "tags": ["萌", "可爱", "甜", "少女", "女"],
            "description": "软萌可爱的少女音。",
        },
        {
            "id": "male-qn-jingying",
            "label": "精英青年音色",
            "gender": "male",
            "language": "zh",
            "tags": ["精英", "沉稳", "成熟", "磁性", "男"],
            "description": "沉稳干练的精英男声。",
        },
        {
            "id": "male-qn-qingse",
            "label": "青涩青年音色",
            "gender": "male",
            "language": "zh",
            "tags": ["少年", "青涩", "清新", "男", "正太"],
            "description": "清新青涩的少年音。",
        },
        {
            "id": "Arrogant_Miss",
            "label": "嚣张小姐",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：嚣张小姐。",
        },
        {
            "id": "Attractive_Girl",
            "label": "Attractive Girl",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：Attractive Girl。",
        },
        {
            "id": "Charming_Lady",
            "label": "Charming Lady",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：Charming Lady。",
        },
        {
            "id": "Chinese (Mandarin)_Crisp_Girl",
            "label": "清脆少女",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "少女"],
            "description": "MiniMax 官方系统音色：清脆少女。",
        },
        {
            "id": "Chinese (Mandarin)_Cute_Spirit",
            "label": "憨憨萌兽",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "萌", "卡通"],
            "description": "MiniMax 官方系统音色：憨憨萌兽。",
        },
        {
            "id": "Chinese (Mandarin)_Gentle_Senior",
            "label": "温柔学姐",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "学姐", "温柔"],
            "description": "MiniMax 官方系统音色：温柔学姐。",
        },
        {
            "id": "Chinese (Mandarin)_HK_Flight_Attendant",
            "label": "港普空姐",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：港普空姐。",
        },
        {
            "id": "Chinese (Mandarin)_Kind-hearted_Antie",
            "label": "热心大婶",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：热心大婶。",
        },
        {
            "id": "Chinese (Mandarin)_Kind-hearted_Elder",
            "label": "花甲奶奶",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：花甲奶奶。",
        },
        {
            "id": "Chinese (Mandarin)_Mature_Woman",
            "label": "傲娇御姐",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "御姐"],
            "description": "MiniMax 官方系统音色：傲娇御姐。",
        },
        {
            "id": "Chinese (Mandarin)_News_Anchor",
            "label": "新闻女声",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：新闻女声。",
        },
        {
            "id": "Chinese (Mandarin)_Soft_Girl",
            "label": "软软女孩",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：软软女孩。",
        },
        {
            "id": "Chinese (Mandarin)_Sweet_Lady",
            "label": "甜美女声",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "甜"],
            "description": "MiniMax 官方系统音色：甜美女声。",
        },
        {
            "id": "Chinese (Mandarin)_Warm_Bestie",
            "label": "温暖闺蜜",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "温柔"],
            "description": "MiniMax 官方系统音色：温暖闺蜜。",
        },
        {
            "id": "Chinese (Mandarin)_Warm_Girl",
            "label": "温暖少女",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "少女", "温柔"],
            "description": "MiniMax 官方系统音色：温暖少女。",
        },
        {
            "id": "Chinese (Mandarin)_Wise_Women",
            "label": "阅历姐姐",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：阅历姐姐。",
        },
        {
            "id": "Cute_Elf",
            "label": "Cute Elf",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "角色", "儿童", "萌"],
            "description": "MiniMax 官方系统音色：Cute Elf。",
        },
        {
            "id": "Serene_Woman",
            "label": "Serene Woman",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女"],
            "description": "MiniMax 官方系统音色：Serene Woman。",
        },
        {
            "id": "Sweet_Girl",
            "label": "Sweet Girl",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "甜"],
            "description": "MiniMax 官方系统音色：Sweet Girl。",
        },
        {
            "id": "cartoon_pig",
            "label": "卡通猪小琪",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "卡通"],
            "description": "MiniMax 官方系统音色：卡通猪小琪。",
        },
        {
            "id": "danya_xuejie",
            "label": "淡雅学姐",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "学姐"],
            "description": "MiniMax 官方系统音色：淡雅学姐。",
        },
        {
            "id": "diadia_xuemei",
            "label": "嗲嗲学妹",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "学妹"],
            "description": "MiniMax 官方系统音色：嗲嗲学妹。",
        },
        {
            "id": "female-chengshu-jingpin",
            "label": "成熟女性音色-beta",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "成熟"],
            "description": "MiniMax 官方系统音色：成熟女性音色-beta。",
        },
        {
            "id": "female-shaonv-jingpin",
            "label": "少女音色-beta",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "少女"],
            "description": "MiniMax 官方系统音色：少女音色-beta。",
        },
        {
            "id": "female-tianmei",
            "label": "甜美女性音色",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "甜"],
            "description": "MiniMax 官方系统音色：甜美女性音色。",
        },
        {
            "id": "female-tianmei-jingpin",
            "label": "甜美女性音色-beta",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "甜"],
            "description": "MiniMax 官方系统音色：甜美女性音色-beta。",
        },
        {
            "id": "female-yujie-jingpin",
            "label": "御姐音色-beta",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "御姐"],
            "description": "MiniMax 官方系统音色：御姐音色-beta。",
        },
        {
            "id": "lovely_girl",
            "label": "萌萌女童",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "儿童", "萌"],
            "description": "MiniMax 官方系统音色：萌萌女童。",
        },
        {
            "id": "tianxin_xiaoling",
            "label": "甜心小玲",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "甜"],
            "description": "MiniMax 官方系统音色：甜心小玲。",
        },
        {
            "id": "wumei_yujie",
            "label": "妩媚御姐",
            "gender": "female",
            "language": "zh",
            "tags": ["zh", "中文", "女", "御姐"],
            "description": "MiniMax 官方系统音色：妩媚御姐。",
        },
        {
            "id": "Arnold",
            "label": "Arnold",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "角色"],
            "description": "MiniMax 官方系统音色：Arnold。",
        },
        {
            "id": "Charming_Santa",
            "label": "Charming Santa",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "角色", "卡通"],
            "description": "MiniMax 官方系统音色：Charming Santa。",
        },
        {
            "id": "Chinese (Mandarin)_Gentle_Youth",
            "label": "温润青年",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年"],
            "description": "MiniMax 官方系统音色：温润青年。",
        },
        {
            "id": "Chinese (Mandarin)_Gentleman",
            "label": "温润男声",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：温润男声。",
        },
        {
            "id": "Chinese (Mandarin)_Humorous_Elder",
            "label": "搞笑大爷",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：搞笑大爷。",
        },
        {
            "id": "Chinese (Mandarin)_Lyrical_Voice",
            "label": "抒情男声",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：抒情男声。",
        },
        {
            "id": "Chinese (Mandarin)_Male_Announcer",
            "label": "播报男声",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "主播"],
            "description": "MiniMax 官方系统音色：播报男声。",
        },
        {
            "id": "Chinese (Mandarin)_Pure-hearted_Boy",
            "label": "清澈邻家弟弟",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：清澈邻家弟弟。",
        },
        {
            "id": "Chinese (Mandarin)_Radio_Host",
            "label": "电台男主播",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "主播"],
            "description": "MiniMax 官方系统音色：电台男主播。",
        },
        {
            "id": "Chinese (Mandarin)_Reliable_Executive",
            "label": "沉稳高管",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：沉稳高管。",
        },
        {
            "id": "Chinese (Mandarin)_Sincere_Adult",
            "label": "真诚青年",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年"],
            "description": "MiniMax 官方系统音色：真诚青年。",
        },
        {
            "id": "Chinese (Mandarin)_Southern_Young_Man",
            "label": "南方小哥",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "少年"],
            "description": "MiniMax 官方系统音色：南方小哥。",
        },
        {
            "id": "Chinese (Mandarin)_Straightforward_Boy",
            "label": "率真弟弟",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：率真弟弟。",
        },
        {
            "id": "Chinese (Mandarin)_Stubborn_Friend",
            "label": "嘴硬竹马",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：嘴硬竹马。",
        },
        {
            "id": "Chinese (Mandarin)_Unrestrained_Young_Man",
            "label": "不羁青年",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年"],
            "description": "MiniMax 官方系统音色：不羁青年。",
        },
        {
            "id": "Grinch",
            "label": "Grinch",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "角色", "卡通"],
            "description": "MiniMax 官方系统音色：Grinch。",
        },
        {
            "id": "Robot_Armor",
            "label": "机械战甲",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "角色"],
            "description": "MiniMax 官方系统音色：机械战甲。",
        },
        {
            "id": "Rudolph",
            "label": "Rudolph",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "角色"],
            "description": "MiniMax 官方系统音色：Rudolph。",
        },
        {
            "id": "badao_shaoye",
            "label": "霸道少爷",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "霸气"],
            "description": "MiniMax 官方系统音色：霸道少爷。",
        },
        {
            "id": "bingjiao_didi",
            "label": "病娇弟弟",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：病娇弟弟。",
        },
        {
            "id": "chunzhen_xuedi",
            "label": "纯真学弟",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "少年"],
            "description": "MiniMax 官方系统音色：纯真学弟。",
        },
        {
            "id": "clever_boy",
            "label": "聪明男童",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "儿童"],
            "description": "MiniMax 官方系统音色：聪明男童。",
        },
        {
            "id": "cute_boy",
            "label": "可爱男童",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "儿童"],
            "description": "MiniMax 官方系统音色：可爱男童。",
        },
        {
            "id": "junlang_nanyou",
            "label": "俊朗男友",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男"],
            "description": "MiniMax 官方系统音色：俊朗男友。",
        },
        {
            "id": "lengdan_xiongzhang",
            "label": "冷淡学长",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "少年", "冷酷"],
            "description": "MiniMax 官方系统音色：冷淡学长。",
        },
        {
            "id": "male-qn-badao",
            "label": "霸道青年音色",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年", "霸气"],
            "description": "MiniMax 官方系统音色：霸道青年音色。",
        },
        {
            "id": "male-qn-badao-jingpin",
            "label": "霸道青年音色-beta",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年", "霸气"],
            "description": "MiniMax 官方系统音色：霸道青年音色-beta。",
        },
        {
            "id": "male-qn-daxuesheng",
            "label": "青年大学生音色",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年"],
            "description": "MiniMax 官方系统音色：青年大学生音色。",
        },
        {
            "id": "male-qn-daxuesheng-jingpin",
            "label": "青年大学生音色-beta",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年"],
            "description": "MiniMax 官方系统音色：青年大学生音色-beta。",
        },
        {
            "id": "male-qn-jingying-jingpin",
            "label": "精英青年音色-beta",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年"],
            "description": "MiniMax 官方系统音色：精英青年音色-beta。",
        },
        {
            "id": "male-qn-qingse-jingpin",
            "label": "青涩青年音色-beta",
            "gender": "male",
            "language": "zh",
            "tags": ["zh", "中文", "男", "青年"],
            "description": "MiniMax 官方系统音色：青涩青年音色-beta。",
        },
        {
            "id": "Cantonese_CuteGirl",
            "label": "可爱女孩",
            "gender": "female",
            "language": "multi",
            "tags": ["多语言", "粤语", "女"],
            "description": "MiniMax 官方系统音色：可爱女孩。",
        },
        {
            "id": "Cantonese_GentleLady",
            "label": "温柔女声",
            "gender": "female",
            "language": "multi",
            "tags": ["多语言", "粤语", "女", "温柔"],
            "description": "MiniMax 官方系统音色：温柔女声。",
        },
        {
            "id": "Cantonese_KindWoman",
            "label": "善良女声",
            "gender": "female",
            "language": "multi",
            "tags": ["多语言", "粤语", "女"],
            "description": "MiniMax 官方系统音色：善良女声。",
        },
        {
            "id": "Cantonese_ProfessionalHost（F)",
            "label": "专业女主持",
            "gender": "female",
            "language": "multi",
            "tags": ["多语言", "粤语", "女", "主播"],
            "description": "MiniMax 官方系统音色：专业女主持。",
        },
        {
            "id": "Cantonese_PlayfulMan",
            "label": "活泼男声",
            "gender": "male",
            "language": "multi",
            "tags": ["多语言", "粤语", "男"],
            "description": "MiniMax 官方系统音色：活泼男声。",
        },
        {
            "id": "Cantonese_ProfessionalHost（M)",
            "label": "专业男主持",
            "gender": "male",
            "language": "multi",
            "tags": ["多语言", "粤语", "男", "主播"],
            "description": "MiniMax 官方系统音色：专业男主持。",
        },
        {
            "id": "Dutch_bossy_leader",
            "label": "Bossy leader",
            "gender": "female",
            "language": "en",
            "tags": ["en", "英文", "女"],
            "description": "MiniMax 官方系统音色：Bossy leader。",
        },
        {
            "id": "Dutch_kindhearted_girl",
            "label": "Kind-hearted girl",
            "gender": "female",
            "language": "en",
            "tags": ["en", "英文", "女"],
            "description": "MiniMax 官方系统音色：Kind-hearted girl。",
        },
        {
            "id": "English_Graceful_Lady",
            "label": "Graceful Lady",
            "gender": "female",
            "language": "en",
            "tags": ["en", "英文", "女"],
            "description": "MiniMax 官方系统音色：Graceful Lady。",
        },
        {
            "id": "English_Whispering_girl",
            "label": "Whispering girl",
            "gender": "female",
            "language": "en",
            "tags": ["en", "英文", "女"],
            "description": "MiniMax 官方系统音色：Whispering girl。",
        },
        {
            "id": "English_Aussie_Bloke",
            "label": "Aussie Bloke",
            "gender": "male",
            "language": "en",
            "tags": ["en", "英文", "男"],
            "description": "MiniMax 官方系统音色：Aussie Bloke。",
        },
        {
            "id": "English_Diligent_Man",
            "label": "Diligent Man",
            "gender": "male",
            "language": "en",
            "tags": ["en", "英文", "男"],
            "description": "MiniMax 官方系统音色：Diligent Man。",
        },
        {
            "id": "English_Gentle-voiced_man",
            "label": "Gentle-voiced man",
            "gender": "male",
            "language": "en",
            "tags": ["en", "英文", "男", "温柔"],
            "description": "MiniMax 官方系统音色：Gentle-voiced man。",
        },
        {
            "id": "English_Trustworthy_Man",
            "label": "Trustworthy Man",
            "gender": "male",
            "language": "en",
            "tags": ["en", "英文", "男"],
            "description": "MiniMax 官方系统音色：Trustworthy Man。",
        },
    ]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    def _request_payload(
        self,
        text: str,
        voice: str,
        fmt: str,
        speed: float | None,
        speech_style: SpeechStyle | None,
    ) -> dict:
        speech_style = (
            speech_style
            if speech_style
            and speech_style.provider == "minimax"
            and speech_style_matches(speech_style, self.provider_name, self.config.model)
            else None
        )
        chosen_voice = voice or pick_catalog_voice(voice, self.VOICE_CATALOG)
        if voice != chosen_voice:
            logger.info("minimax tts: substituted voice", extra={"requested": voice, "used": chosen_voice})
        return {
            "model": self.config.model,
            "text": styled_speech_text(text, speech_style, provider=self.provider_name, model=self.config.model),
            "voice_setting": {
                "voice_id": chosen_voice,
                "speed": speed if speed is not None else speech_style.speed if speech_style else 1.0,
                "vol": 1.0,
                "pitch": 0,
                **({"emotion": speech_style.emotion} if speech_style and speech_style.emotion else {}),
            },
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": fmt, "channel": 1},
        }

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
        speech_style: SpeechStyle | None = None,
    ) -> TTSResult:
        payload = self._request_payload(text, voice, fmt, speed, speech_style)
        resp = await self._client.post("/v1/t2a_v2", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        audio = extract_minimax_audio(body)
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        return TTSResult(audio=audio, mime=mime, voice=payload["voice_setting"]["voice_id"])

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice: str = "",
        speed: float | None = None,
        speech_style: SpeechStyle | None = None,
    ) -> AsyncIterator[AudioChunk]:
        payload = self._request_payload(text, voice, "pcm", speed, speech_style)
        payload.update(stream=True, stream_options={"exclude_aggregated_audio": True})
        async with self._client.stream("POST", "/v1/t2a_v2", json=payload, timeout=_STREAM_TIMEOUT) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    body = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if not isinstance(body, dict):
                    continue
                raise_for_minimax_stream_event(body, provider="minimax", model=self.config.model)
                hex_audio = (body.get("data") or {}).get("audio") or ""
                if hex_audio:
                    yield AudioChunk(bytes.fromhex(hex_audio), "audio/pcm", sample_rate=32000)

    async def design_voice(self, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
        payload: dict = {"prompt": prompt, "preview_text": preview_text or "你好，我是你的桌面伙伴。"}
        resp = await self._client.post("/v1/voice_design", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        voice_id = body.get("voice_id", "")
        if not voice_id:
            raise RuntimeError("MiniMax voice design returned no voice_id")
        trial_hex = body.get("trial_audio", "")
        if not trial_hex:
            raise RuntimeError("MiniMax voice design returned no trial_audio")
        return VoiceDesignResult(
            voice_id=voice_id,
            trial_audio=bytes.fromhex(trial_hex),
            trial_audio_mime="audio/mpeg",
            provider=self.provider_name,
        )
