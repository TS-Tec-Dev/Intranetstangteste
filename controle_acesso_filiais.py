"""Validação das filiais permitidas no cadastro de cada usuário.

Nunca use o atributo Admin como autorização universal sobre todas as filiais.
O cadastro banco_usuarios.csv persiste no banco externo da Intranet.
"""
from __future__ import annotations


def filiais_permitidas(cadastro, filiais_ativas):
    """Retorna somente os códigos ativos associados ao usuário, na ordem do cadastro."""
    disponiveis = {f["codigo"] for f in filiais_ativas if f.get("ativo", 1)}
    texto = str(cadastro.get("Filiais", "") or "")
    vistos = set()
    resultado = []
    for item in texto.split(","):
        codigo = item.strip()
        if codigo in disponiveis and codigo not in vistos:
            vistos.add(codigo)
            resultado.append(codigo)
    return resultado


def conferir_filial(cadastro, filial, filiais_ativas):
    """Bloqueia qualquer filial que não conste das permissões individuais."""
    autorizadas = filiais_permitidas(cadastro, filiais_ativas)
    if filial not in autorizadas:
        raise PermissionError("Seu usuário não tem permissão para acessar esta filial.")
    return autorizadas


def pode_gerir_todas_as_filiais(cadastro, filiais_ativas):
    """Ações globais de contas/filiais só para administrador com TODAS as filiais autorizadas."""
    if str(cadastro.get("Admin", "Não")).strip().casefold() != "sim":
        return False
    codigos = {f["codigo"] for f in filiais_ativas if f.get("ativo", 1)}
    return bool(codigos) and codigos <= set(filiais_permitidas(cadastro, filiais_ativas))


def validar_sessao(usuario, filial, repositorio):
    """Reconfere a autorização NO BANCO ao abrir Filiais e Backup."""
    import pandas as pd
    cadastros = repositorio.ler_csv("banco_usuarios.csv", dtype=str)
    if not usuario or cadastros.empty or "Usuario" not in cadastros:
        raise PermissionError("Sessão sem cadastro de usuário válido.")
    encontrados = cadastros[cadastros["Usuario"].astype(str).str.casefold() == str(usuario).casefold()]
    if encontrados.empty:
        raise PermissionError("Usuário não encontrado no cadastro de acessos.")
    cadastro = encontrados.iloc[0].to_dict()
    if str(cadastro.get("Admin", "Não")).strip().casefold() != "sim":
        raise PermissionError("Esta área é reservada a administradores autorizados.")
    ativas = repositorio.listar_filiais()
    permitidas = conferir_filial(cadastro, filial, ativas)
    return cadastro, ativas, permitidas, pode_gerir_todas_as_filiais(cadastro, ativas)
