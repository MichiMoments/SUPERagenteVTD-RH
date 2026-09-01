def extraer_texto(content):
    """Extrae texto plano del content de un AIMessage (puede ser str o lista de bloques)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        partes = []
        for bloque in content:
            if isinstance(bloque, dict) and bloque.get("type") == "text":
                partes.append(bloque.get("text", ""))
            elif isinstance(bloque, str):
                partes.append(bloque)
        return "\n".join(partes)
    return str(content)
