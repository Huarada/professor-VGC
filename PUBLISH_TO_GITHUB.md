# Publicar o ProfessorVGC no seu GitHub (repositório privado)

Não consegui criar o repositório direto da minha sessão: não há um conector do
GitHub conectado e o `api.github.com` está bloqueado pelo proxy do ambiente.
Então te entrego o projeto como um **Git bundle** (`oracle-vgc.bundle`) — um
único arquivo que contém todos os arquivos **e o histórico do commit**. Escolha
uma das opções abaixo (leva ~1 minuto).

Baixe primeiro `oracle-vgc.bundle` e `publish_github.sh` para uma pasta no seu
computador.

---

## Opção A — automática, com GitHub CLI (`gh`) — recomendada

Pré-requisito: ter o `gh` instalado e logado (`gh auth login`).

```bash
bash publish_github.sh oracle-vgc            # nome do repo (privado por padrão)
```

O script clona o bundle, cria o repositório **privado** na sua conta e faz o push.

---

## Opção B — manual (só git, sem gh)

1. Crie um repositório **privado** vazio em https://github.com/new
   (nome: `oracle-vgc`, **sem** README/gitignore/license).

2. No terminal, dentro da pasta onde está `oracle-vgc.bundle`:

```bash
git clone oracle-vgc.bundle oracle-vgc
cd oracle-vgc
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/oracle-vgc.git
git push -u origin main
```

Troque `SEU_USUARIO` pelo seu usuário do GitHub. Se pedir senha no push, use um
**Personal Access Token** (Settings → Developer settings → Tokens) como senha.

---

## Depois de publicar

```bash
cd oracle-vgc
python -m pip install -r requirements.txt
cd node_calc && npm install && cd ..
cp .env.example .env        # preencha PROFESSORVGC_OPENAI_API_KEY ou PROFESSORVGC_GEMINI_API_KEY
pytest                      # 27 testes, sem precisar de chave
streamlit run src/ui/app.py
```

`node_modules/` e `.env` já estão no `.gitignore` — não vão para o repositório.
