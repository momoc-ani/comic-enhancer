import re


# 方法说明：规范化作品标题以便稳定匹配。
def normalize_title(value: str) -> str:
    return re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff]+", "", value.casefold())


# 方法说明：规范化角色名称以便别名匹配。
def normalize_character_name(value: str) -> str:
    return normalize_title(value)


# 方法说明：判断作品标题是否完整匹配登记别名。
def alias_title_matches(title: str, alias: str) -> bool:
    if len(alias) < 8:
        return title == alias
    return title == alias or title.startswith(alias) or title.endswith(alias)
