from bs4 import BeautifulSoup
from typing import List, Dict, Any
from dateutil.parser import parse as date_parse
import datetime


def _make_aware(dt: datetime.datetime) -> datetime.datetime:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def parse_date(value: str):
    try:
        dt = date_parse(value, dayfirst=True)
        return _make_aware(dt)
    except Exception:
        return None


def get_especificacao(soup: BeautifulSoup) -> str:
    el = soup.select_one('#txtDescricao')
    return el.get('value') if el else ''


def get_tipo_procedimento(soup: BeautifulSoup) -> Dict[str, Any]:
    option = soup.select_one('#selTipoProcedimento option[selected]')
    if not option:
        option = soup.select_one('#selTipoProcedimento option:checked')
    if option:
        return {"id": option.get('value'), "nome": option.text.strip()}
    return {"id": None, "nome": None}


def get_assunto(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    return [{"id": opt.get('value'), "nome": opt.text.strip()} for opt in soup.select('#selAssuntos option')]


def get_interessados(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    return [{"id": opt.get('value'), "nome": opt.text.strip()} for opt in soup.select('#selInteressadosProcedimento option')]


def get_observacoes(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    observacoes = []
    linhas = soup.select('table.infraTable tbody tr.infraTrClara')
    for linha in linhas:
        colunas = linha.select('td')
        if len(colunas) >= 2:
            unidade_el = colunas[0].select_one('.ancoraSigla')
            unidade = unidade_el.text.strip() if unidade_el else ''
            observacao = colunas[1].text.strip()
            observacoes.append({"unidade": unidade, "observacao": observacao})
    return observacoes


def get_anexos(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    anexos = []
    el_anexos = soup.select('.infraArvore a[target="ifrConteudoVisualizacao"]')
    for el_anexo in el_anexos[1:]:
        nome_anexo = el_anexo.text.strip()
        sibling = el_anexo.find_next_sibling()
        nome_unidade = sibling.text.strip() if sibling else ''
        anexos.append({"nome": nome_anexo, "unidade": nome_unidade,})
    return anexos


def get_processos_relacionados(soup: BeautifulSoup) -> List[str]:
    el_relacionados = soup.find_all('p', class_='cardTitleRelacionado')
    return [el_relacionado.get_text().replace('.', '').replace('/', '').replace('-', '') for el_relacionado in el_relacionados]


def parse_metadata_from_frames(ifr_visualizacao_content: str, ifr_arvore_content: str, numero_processo: str) -> Dict[str, Any]:
    from bs4 import BeautifulSoup
    soup_vis = BeautifulSoup(ifr_visualizacao_content, 'html.parser')
    soup_arv = BeautifulSoup(ifr_arvore_content, 'html.parser')
    
    especificacao = get_especificacao(soup_vis)
    tipo_procedimento = get_tipo_procedimento(soup_vis)
    assunto = get_assunto(soup_vis)
    interessados = get_interessados(soup_vis)
    observacoes = get_observacoes(soup_vis)
    anexos = get_anexos(soup_arv)
    processos_relacionados = get_processos_relacionados(soup_arv)

    return {
        'protocolo': numero_processo,
        'especificacao': especificacao,
        'tipo_procedimento': tipo_procedimento,
        'assunto': assunto,
        'interessados': interessados,
        'observacoes': observacoes,
        'anexos': anexos,
        'processos_relacionados': processos_relacionados,
    }
