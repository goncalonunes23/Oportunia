from fpdf import FPDF
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import os
import json
from datetime import datetime
from functools import wraps
from openai import AzureOpenAI  
from dotenv import load_dotenv  #Carregar biblioteca para ler o ficheiro .env
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
import smtplib
from email.message import EmailMessage
import PyPDF2
import io
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient

# Carrega as variáveis de ambiente a partir do ficheiro .env local
load_dotenv()

app = Flask(__name__)

# Configurações dinâmicas lidas de forma segura a partir do ambiente local (.env)
app.secret_key = os.environ.get("SECRET_KEY", "chave-secreta-de-desenvolvimento")
serializer = URLSafeTimedSerializer(app.secret_key)

MONGO_URI = os.environ.get("LIGACAO_COSMOS", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI, tlsAllowInvalidCertificates=True)
db = client["OportuniaDB"]

# Inicialização segura do cliente Azure OpenAI sem expor dados no código fonte
AI_CLIENT = AzureOpenAI(
    api_key=os.environ.get("AZURE_OPENAI_KEY"),
    api_version="2024-12-01-preview",
    azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT")
)

# ─────────────────────────────────────────────
# Decorator: protege rotas que requerem login
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "utilizador_email" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
# PÁGINAS PÚBLICAS
# ─────────────────────────────────────────────

@app.route("/")
def index():
    logado = "utilizador_email" in session
    nome = session.get("utilizador_nome", "") if logado else ""
    return render_template("index.html", logado=logado, nome_utilizador=nome)


@app.route("/login", methods=["GET"])
def login():
    if "utilizador_email" in session:
        return redirect(url_for("oportunidades"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_post():
    try:
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("login.html", erro="Preenche todos os campos.")

        utilizador = db["Utilizadores"].find_one({"email": email})

        if not utilizador or not check_password_hash(utilizador["password_hash"], password):
            return render_template("login.html", erro="Email ou password incorretos.")

        session["utilizador_email"] = email
        session["utilizador_nome"] = utilizador.get("nome", "Utilizador")

        return redirect(url_for("oportunidades"))
    except Exception as e:
        import traceback
        erro_detalhado = traceback.format_exc()
        return f"<pre>Erro 500 Detalhado no Login:\n{erro_detalhado}</pre>", 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/registo", methods=["GET"])
def registo():
    if "utilizador_email" in session:
        return redirect(url_for("oportunidades"))
    return render_template("registo.html")


@app.route("/registo", methods=["POST"])
def registo_post():
    nome = request.form.get("nome", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not nome or not email or not password:
        return render_template("registo.html", erro="Preenche todos os campos.")

    if len(password) < 8:
        return render_template("registo.html", erro="A password deve ter pelo menos 8 caracteres.")

    if db["Utilizadores"].find_one({"email": email}):
        return render_template("registo.html", erro="Este email já está registado.")

    novo_utilizador = {
        "email": email,
        "password_hash": generate_password_hash(password),
        "nome": nome,
        "headline": "Estudante de Engenharia Informática",
        "instituicao": "Instituto Politécnico de Castelo Branco",
        "curso": "Licenciatura em Engenharia Informática",
        "links": {
            "github": "",
            "linkedin": "",
            "website": ""
        },
        "competencias_tecnicas": [],
        "cadeiras_favoritas": [],
        "localizacoes_preferidas": [],
        "tipos_oportunidade": [],
        "disponibilidade": "",
        "notificacoes": {
            "email_alertas": True,
            "relatorio_semanal": False
        },
        "data_registo": datetime.utcnow().isoformat(),
        "data_atualizacao": datetime.utcnow().isoformat()
    }

    db["Utilizadores"].insert_one(novo_utilizador)

    session["utilizador_email"] = email
    session["utilizador_nome"] = nome

    return redirect(url_for("perfil"))


# ─────────────────────────────────────────────
# PÁGINAS PRIVADAS
# ─────────────────────────────────────────────

@app.route("/oportunidades")
@login_required
def oportunidades():
    try:
        vagas = list(db["Vagas"].find({}, {"_id": 0}))
    except Exception as e:
        print(f"Erro ao obter vagas: {e}")
        vagas = []

    nome = session.get("utilizador_nome", "Utilizador")
    return render_template("dashboard.html", vagas=vagas, nome_utilizador=nome)


@app.route("/perfil", methods=["GET"])
@login_required
def perfil():
    email = session["utilizador_email"]
    utilizador = db["Utilizadores"].find_one({"email": email}, {"_id": 0, "password_hash": 0})

    if not utilizador:
        return redirect(url_for("logout"))

    return render_template("perfil.html", utilizador=utilizador)


# ─────────────────────────────────────────────
# API ENDPOINTS
# ─────────────────────────────────────────────

@app.route("/api/vagas", methods=["GET"])
def get_vagas_api():
    try:
        vagas = list(db["Vagas"].find({}, {"_id": 0}))
    except Exception as e:
        return jsonify({"erro": str(e)}), 500
    return jsonify(vagas)


@app.route("/api/perfil", methods=["GET"])
@login_required
def get_perfil():
    email = session["utilizador_email"]
    utilizador = db["Utilizadores"].find_one({"email": email}, {"_id": 0, "password_hash": 0})
    if not utilizador:
        return jsonify({"erro": "Utilizador não encontrado"}), 404
    return jsonify(utilizador)


@app.route("/api/recomendacoes", methods=["GET"])
@login_required
def get_recomendacoes():
    """
    Endpoint assíncrono chamado pela dashboard.
    Pede ao GPT-4o que analise o perfil do utilizador e devolva
    as vagas ordenadas por relevância com uma justificação personalizada
    para cada uma, em JSON estruturado.
    """
    email = session["utilizador_email"]

    # 1. Carregar perfil completo do utilizador
    utilizador = db["Utilizadores"].find_one({"email": email}, {"_id": 0, "password_hash": 0})
    if not utilizador:
        return jsonify({"erro": "Utilizador não encontrado"}), 404

    # 2. Buscar todas as vagas disponíveis
    try:
        vagas = list(db["Vagas"].find({}, {"_id": 0}))
    except Exception as e:
        return jsonify({"erro": f"Erro ao carregar vagas: {str(e)}"}), 500

    if not vagas:
        return jsonify({"vagas": [], "perfil_completo": False, "mensagem": "Sem vagas disponíveis."})

    # 3. Verificar se o perfil tem dados suficientes para personalizar
    competencias = utilizador.get("competencias_tecnicas", [])
    cadeiras = utilizador.get("cadeiras_favoritas", [])
    tipos = utilizador.get("tipos_oportunidade", [])
    localizacoes = utilizador.get("localizacoes_preferidas", [])
    perfil_completo = bool(competencias or cadeiras or tipos)

    # 4. Limitar a 20 vagas enviadas à IA (evitar exceder limites de tokens)
    #    Priorizar vagas cuja localização ou tipo coincidem com as preferências do utilizador
    def rank_vaga(v):
        score = 0
        v_local = str(v.get("local") or v.get("localizacao", "")).lower()
        v_tipo = str(v.get("tipo", "")).lower()
        
        # Prioridade máxima: Localização (ou Remoto)
        if localizacoes:
            locs_lower = [l.lower() for l in localizacoes]
            # Se a vaga contiver alguma das localizações escolhidas ou for remota
            if any(l in v_local for l in locs_lower) or "remoto" in v_local:
                score += 2
                
        # Prioridade média: Tipo de Oportunidade
        if tipos:
            tipos_lower = [t.lower() for t in tipos]
            if any(t in v_tipo for t in tipos_lower):
                score += 1
                
        return score

    # Ordenar as vagas por score (maior para menor)
    if localizacoes or tipos:
        vagas.sort(key=rank_vaga, reverse=True)

    vagas_para_ia = vagas[:20]
    vagas_restantes = vagas[20:]

    # 5. Construir resumo compacto das vagas para o prompt
    vagas_resumo = []
    for v in vagas_para_ia:
        vagas_resumo.append({
            "id_vaga": v.get("id_vaga"),
            "titulo": v.get("titulo", ""),
            "empresa": v.get("empresa", ""),
            "tipo": v.get("tipo", ""),
            "local": v.get("local") or v.get("localizacao", ""),
            "tags": v.get("tags", []),
            "descricao": (v.get("descricao", "") or "")[:1000]
        })

    prompt_sistema = (
        "És um assistente de carreira integrado no portal Oportunia. "
        "Analisa o perfil de um estudante e classifica oportunidades por relevância. "
        'Responde SEMPRE com um objeto JSON com a chave "recomendacoes" contendo um array de objetos.'
    )

    prompt_utilizador = f"""Perfil do Estudante:
- Nome: {utilizador.get('nome', 'Estudante')}
- Curso: {utilizador.get('curso', 'Não especificado')} em {utilizador.get('instituicao', 'Não especificada')}
- Competências Técnicas: {', '.join(competencias) if competencias else 'Não especificadas'}
- Cadeiras Favoritas: {', '.join(cadeiras) if cadeiras else 'Não especificadas'}
- Localizações Preferidas: {', '.join(localizacoes) if localizacoes else 'Qualquer'}
- Tipos de Oportunidade: {', '.join(tipos) if tipos else 'Qualquer'}
- Disponibilidade: {utilizador.get('disponibilidade', 'Não especificada')}

Oportunidades a analisar ({len(vagas_resumo)}):
{json.dumps(vagas_resumo, ensure_ascii=False)}

Devolve um objeto JSON com a seguinte estrutura exata:
{{
  "recomendacoes": [
    {{
      "titulo": "título exato da oportunidade (copia exatamente)",
      "score": <inteiro de 1 a 10>,
      "justificacao_ai": "<2 frases em português explicando a adequação>",
      "tags_extraidas": ["React", "TypeScript", "Python"],
      "requisitos_resumo": ["3 anos de experiência", "Inglês B2"]
    }}
  ]
}}
Ordena do score mais alto para o mais baixo. Inclui todas as {len(vagas_resumo)} oportunidades. "tags_extraidas" devem ser no máximo 5 tecnologias essenciais da vaga. "requisitos_resumo" devem ser no máximo 4 pontos com as principais exigências listadas na descrição da vaga.
"""

    # 6. Chamar GPT-4o (ou usar cache se existir)
    recomendacoes_ia = utilizador.get("cache_recomendacoes_ia", [])
    erro_ia = None

    if not recomendacoes_ia and perfil_completo:
        try:
            resposta = AI_CLIENT.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": prompt_utilizador}
                ],
                max_tokens=4000,
                temperature=0.5,
                response_format={"type": "json_object"}
            )
            conteudo = resposta.choices[0].message.content
            parsed = json.loads(conteudo)
            # Extrair a lista de recomendações do objeto devolvido
            if isinstance(parsed, list):
                recomendacoes_ia = parsed
            elif isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        recomendacoes_ia = v
                        break
            
            # Guardar em cache para a próxima vez
            if recomendacoes_ia:
                db["Utilizadores"].update_one(
                    {"email": email}, 
                    {"$set": {"cache_recomendacoes_ia": recomendacoes_ia}}
                )
                
        except Exception as ex:
            erro_ia = str(ex)
            recomendacoes_ia = []
            print(f"[ERRO IA] {erro_ia}")

    # 7. Fazer merge: justificações IA com dados completos das vagas
    vagas_por_titulo = {v.get("titulo", ""): v for v in vagas}
    vagas_finais = []
    titulos_processados = set()

    for rec in recomendacoes_ia:
        if not isinstance(rec, dict):
            continue
        titulo_rec = rec.get("titulo", "")
        if titulo_rec in vagas_por_titulo:
            vaga_completa = dict(vagas_por_titulo[titulo_rec])
            vaga_completa["justificacao_ai"] = rec.get("justificacao_ai", "")
            vaga_completa["score_ia"] = rec.get("score", 0)
            vaga_completa["tags"] = rec.get("tags_extraidas", [])
            vaga_completa["requisitos"] = rec.get("requisitos_resumo", [])
            vagas_finais.append(vaga_completa)
            titulos_processados.add(titulo_rec)

    # Vagas analisadas pela IA mas não devolvidas (falha de match) + restantes
    for vaga in vagas:
        if vaga.get("titulo", "") not in titulos_processados:
            vaga_copia = dict(vaga)
            vaga_copia["justificacao_ai"] = ""
            vaga_copia["score_ia"] = 0
            vagas_finais.append(vaga_copia)

    return jsonify({
        "vagas": vagas_finais,
        "perfil_completo": perfil_completo,
        "erro_ia": erro_ia,
        "nome_utilizador": utilizador.get("nome", "Utilizador")
    })


@app.route("/api/parse_cv", methods=["POST"])
@login_required
def parse_cv():
    if "cv" not in request.files:
        return jsonify({"erro": "Nenhum ficheiro recebido."}), 400
        
    file = request.files["cv"]
    if file.filename == "":
        return jsonify({"erro": "Nenhum ficheiro selecionado."}), 400
        
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"erro": "Apenas são suportados ficheiros PDF."}), 400

    try:
        pdf_reader = PyPDF2.PdfReader(file)
        texto_cv = ""
        for page in pdf_reader.pages:
            texto_cv += page.extract_text() + "\n"
            
        if not texto_cv.strip():
            return jsonify({"erro": "Não foi possível extrair texto deste PDF (poderá ser uma imagem)."}), 400

        prompt_sistema = "És um assistente de Recursos Humanos especialista em extração de currículos."
        prompt_utilizador = f"""
Extrai as seguintes informações deste CV:
1. Nome da Instituição de ensino atual ou mais recente (String).
2. Nome do Curso ou Licenciatura/Mestrado (String).
3. Competências Técnicas (Hard Skills, Tecnologias, Frameworks) (Lista de Strings).
4. Idiomas falados (Lista de Strings).
5. Projetos relevantes (Lista de Strings curtas).

Devolve ESTRITAMENTE um objeto JSON válido, sem markdown, sem blocos de código (```json), com esta exata estrutura:
{{
  "instituicao": "nome",
  "curso": "nome",
  "competencias": ["React", "Python"],
  "linguas": ["Inglês", "Espanhol"],
  "projetos": ["Projeto A", "Sistema B"]
}}

Texto do CV:
{texto_cv}
"""
        resposta = AI_CLIENT.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_utilizador}
            ],
            max_tokens=1000,
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        conteudo = resposta.choices[0].message.content
        dados_extraidos = json.loads(conteudo)
        
        # ─────────────────────────────────────────────
        # Integração Azure Blob Storage
        # ─────────────────────────────────────────────
        email_utilizador = session.get("utilizador_email")
        if email_utilizador:
            try:
                conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
                if conn_str:
                    blob_service_client = BlobServiceClient.from_connection_string(conn_str)
                    container_name = "curriculos"
                    
                    container_client = blob_service_client.get_container_client(container_name)
                    if not container_client.exists():
                        container_client.create_container()
                        
                    original_name = secure_filename(file.filename)
                    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                    prefix = email_utilizador.split('@')[0]
                    blob_name = f"{prefix}_{timestamp}_{original_name}"
                    
                    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
                    file.seek(0)
                    blob_client.upload_blob(file.read(), overwrite=True)
                    
                    db["Utilizadores"].update_one(
                        {"email": email_utilizador}, 
                        {"$set": {"cv_blob_name": blob_name, "cv_original_name": file.filename}}
                    )
                else:
                    print("AVISO: AZURE_STORAGE_CONNECTION_STRING não definida. Ficheiro não guardado na Cloud.")
            except Exception as blob_err:
                print(f"Erro ao gravar no Blob Storage: {blob_err}")

        return jsonify({
            "sucesso": True,
            "dados": dados_extraidos
        })
        
    except Exception as e:
        print(f"Erro ao analisar CV: {e}")
        return jsonify({"erro": "Falha na análise do CV com a IA."}), 500

@app.route("/api/cv/download", methods=["GET"])
@login_required
def download_cv():
    email = session["utilizador_email"]
    utilizador = db["Utilizadores"].find_one({"email": email})
    
    if not utilizador or "cv_blob_name" not in utilizador:
        return jsonify({"erro": "Nenhum currículo guardado."}), 404
        
    blob_name = utilizador["cv_blob_name"]
    original_name = utilizador.get("cv_original_name", "curriculo.pdf")
    
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        return jsonify({"erro": "Configuração de Cloud Storage em falta no servidor."}), 500
        
    try:
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        blob_client = blob_service_client.get_blob_client(container="curriculos", blob=blob_name)
        
        download_stream = blob_client.download_blob()
        file_content = download_stream.readall()
        
        return app.response_class(
            file_content,
            headers={"Content-Disposition": f"attachment; filename={original_name}"},
            mimetype="application/pdf"
        )
    except Exception as e:
        print(f"Erro ao descarregar CV: {e}")
        return jsonify({"erro": "Falha ao descarregar o ficheiro da Cloud."}), 500


@app.route("/api/perfil", methods=["POST"])
@login_required
def save_perfil():
    email = session["utilizador_email"]
    dados = request.get_json()

    campos_permitidos = [
        "nome", "headline", "instituicao", "curso",
        "links", "competencias_tecnicas", "cadeiras_favoritas",
        "localizacoes_preferidas", "tipos_oportunidade",
        "disponibilidade", "notificacoes", "linguas", "projetos"
    ]

    atualizacao = {k: dados[k] for k in campos_permitidos if k in dados}
    atualizacao["data_atualizacao"] = datetime.utcnow().isoformat()

    if not atualizacao:
        return jsonify({"erro": "Nenhum campo válido para atualizar"}), 400

    # 1. Atualizar os dados do perfil no MongoDB Cosmos DB
    # Limpar a cache da IA porque o perfil mudou
    db["Utilizadores"].update_one(
        {"email": email}, 
        {
            "$set": atualizacao,
            "$unset": {"cache_recomendacoes_ia": ""}
        }
    )

    if "nome" in atualizacao:
        session["utilizador_nome"] = atualizacao["nome"]

    return jsonify({
        "sucesso": True, 
        "mensagem": "Perfil atualizado com sucesso"
    })


@app.route("/api/credenciais", methods=["POST"])
@login_required
def update_credenciais():
    email = session["utilizador_email"]
    dados = request.get_json()

    atualizacao = {}

    if "novo_email" in dados:
        novo_email = dados["novo_email"].strip().lower()
        if not novo_email:
            return jsonify({"erro": "Email inválido."}), 400
        if db["Utilizadores"].find_one({"email": novo_email}):
            return jsonify({"erro": "Este email já está em uso."}), 400
        atualizacao["email"] = novo_email

    if "nova_password" in dados:
        nova_password = dados["nova_password"]
        if len(nova_password) < 8:
            return jsonify({"erro": "A password deve ter pelo menos 8 caracteres."}), 400
        atualizacao["password_hash"] = generate_password_hash(nova_password)

    if not atualizacao:
        return jsonify({"erro": "Nada para atualizar."}), 400

    atualizacao["data_atualizacao"] = datetime.utcnow().isoformat()
    db["Utilizadores"].update_one({"email": email}, {"$set": atualizacao})

    if "email" in atualizacao:
        session["utilizador_email"] = atualizacao["email"]

    return jsonify({"sucesso": True})


@app.route("/api/recuperar-password", methods=["POST"])
def recuperar_password():
    dados = request.get_json()
    email = dados.get("email", "").strip().lower()
    
    if not email:
        return jsonify({"erro": "Email inválido."}), 400
        
    utilizador = db["Utilizadores"].find_one({"email": email})
    if utilizador:
        # Gera o token seguro
        token = serializer.dumps(email, salt='recuperacao-password')
        link_recuperacao = url_for('reset_password_page', token=token, _external=True)
        
        # Envia o email
        try:
            enviar_email_recuperacao(email, link_recuperacao)
        except Exception as e:
            print(f"Erro ao enviar email de recuperação: {e}")
            return jsonify({"erro": "Ocorreu um erro ao enviar o email de recuperação."}), 500
    
    return jsonify({"sucesso": True, "mensagem": "Se o email existir, enviámos as instruções para recuperares a password."})

def enviar_email_recuperacao(email_destino, link):
    remetente = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")
    servidor = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    porta = int(os.environ.get("MAIL_PORT", 587))
    
    if not remetente or not password:
        raise Exception("Credenciais de email não configuradas no .env")

    msg = EmailMessage()
    msg['Subject'] = 'Oportunia - Recuperação de Password'
    msg['From'] = remetente
    msg['To'] = email_destino
    msg.set_content(f"""Olá,
    
Recebemos um pedido para redefinir a tua password no Oportunia.
Clica no link abaixo para criar uma nova password (o link expira em 1 hora):

{link}

Se não pediste para redefinir a password, podes ignorar este email.
    
A Equipa Oportunia""")

    with smtplib.SMTP(servidor, porta) as s:
        s.starttls()
        s.login(remetente, password)
        s.send_message(msg)

@app.route("/reset-password/<token>", methods=["GET"])
def reset_password_page(token):
    try:
        serializer.loads(token, salt='recuperacao-password', max_age=3600) # Expira em 1 hora
    except SignatureExpired:
        return render_template("reset_password.html", erro="O link de recuperação expirou. Por favor, pede um novo.")
    except BadTimeSignature:
        return render_template("reset_password.html", erro="Link de recuperação inválido.")
    
    return render_template("reset_password.html", token=token)

@app.route("/reset-password/<token>", methods=["POST"])
def reset_password_post(token):
    try:
        email = serializer.loads(token, salt='recuperacao-password', max_age=3600)
    except SignatureExpired:
        return render_template("reset_password.html", erro="O link de recuperação expirou. Por favor, pede um novo.")
    except BadTimeSignature:
        return render_template("reset_password.html", erro="Link de recuperação inválido.")
        
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")
    
    if len(password) < 8:
        return render_template("reset_password.html", token=token, erro="A password deve ter pelo menos 8 caracteres.")
        
    if password != confirm_password:
        return render_template("reset_password.html", token=token, erro="As passwords não coincidem.")
        
    # Atualiza a base de dados
    db["Utilizadores"].update_one(
        {"email": email}, 
        {
            "$set": {
                "password_hash": generate_password_hash(password),
                "data_atualizacao": datetime.utcnow().isoformat()
            }
        }
    )
    
    return render_template("reset_password.html", sucesso="Password redefinida com sucesso!")

@app.route("/api/gerar-relatorio", methods=["POST"])
@login_required
def gerar_relatorio():
    email = session["utilizador_email"]
    utilizador = db["Utilizadores"].find_one({"email": email})
    
    # 1. Obter as recomendações em cache ou da BD
    vagas = utilizador.get("cache_recomendacoes_ia", [])
    if not vagas:
        return jsonify({"erro": "Não tens recomendações recentes para gerar um relatório."}), 400
        
    # 2. Criar o PDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, f"Oportunia - Relatório de Oportunidades", ln=True, align='C')
    pdf.set_font("Arial", '', 12)
    pdf.cell(0, 10, f"Estudante: {utilizador.get('nome', 'Desconhecido')}", ln=True, align='C')
    pdf.ln(10)
    
    # Adicionar as 5 melhores vagas ao PDF
    for vaga in vagas[:5]:
        pdf.set_font("Arial", 'B', 12)
        # Atenção a caracteres especiais; o encode/decode evita erros no FPDF
        titulo = vaga.get('titulo', 'Vaga').encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(0, 8, f"- {titulo}", ln=True)
        
        pdf.set_font("Arial", '', 10)
        justificacao = vaga.get('justificacao_ai', '').encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 6, f"Porquê: {justificacao}")
        pdf.ln(5)
        
    pdf_bytes = pdf.output(dest='S').encode('latin1') # PDF em memória
    
    # 3. Guardar no Blob Storage
    try:
        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        container_client = blob_service_client.get_container_client("relatorios")
        
        if not container_client.exists():
            container_client.create_container()
            
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M")
        blob_name = f"relatorio_{email.split('@')[0]}_{timestamp}.pdf"
        
        blob_client = blob_service_client.get_blob_client(container="relatorios", blob=blob_name)
        blob_client.upload_blob(pdf_bytes, overwrite=True)
        
        # Opcional: guardar o nome do ficheiro no histórico do utilizador na BD
        return jsonify({"sucesso": True, "mensagem": "Relatório gerado e guardado com sucesso!", "ficheiro": blob_name})
        
    except Exception as e:
        print(f"Erro no Blob Storage: {e}")
        return jsonify({"erro": "Falha ao guardar o relatório na cloud."}), 500
    

    @app.route("/api/relatorios/download/<blob_name>", methods=["GET"])
@login_required
def download_relatorio(blob_name):
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        return jsonify({"erro": "Configuração de Cloud Storage em falta no servidor."}), 500
        
    try:
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
        # Atenção para usar o nome do contentor certo: "relatorios"
        blob_client = blob_service_client.get_blob_client(container="relatorios", blob=blob_name)
        
        download_stream = blob_client.download_blob()
        file_content = download_stream.readall()
        
        return app.response_class(
            file_content,
            headers={"Content-Disposition": f"attachment; filename={blob_name}"},
            mimetype="application/pdf"
        )
    except Exception as e:
        print(f"Erro ao descarregar Relatório PDF: {e}")
        return jsonify({"erro": "Falha ao descarregar o ficheiro da Cloud."}), 500

        
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, use_reloader=False)
