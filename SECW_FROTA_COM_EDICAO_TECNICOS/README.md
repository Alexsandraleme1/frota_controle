# SECW FROTA — versão completa (v2)

Sistema web interno em Python, Flask e SQLite para gestão de frota, com interface responsiva na identidade visual SEC.W.

## Recursos desta versão

### Painel e alertas
- Indicadores de veículos disponíveis, em uso e em manutenção.
- Alertas de documentos e seguros vencidos ou a vencer nos próximos 30 dias.
- Alertas de revisão por data e por quilometragem.
- Despesas do mês: combustível, manutenção e multas não canceladas.

### Veículos e histórico
- Cadastro com placa, marca, modelo, ano, cor, patrimônio, quilometragem, RENAVAM, chassi, data de aquisição e vencimento do seguro.
- Foto do veículo (JPG, PNG ou WEBP), exibida na lista e no histórico.
- Histórico de saídas/devoluções, abastecimentos, manutenções, documentos, multas e vistorias.
- Pesquisa rápida nas tabelas.

### Utilização e vistoria
- Retirada autônoma: o técnico vinculado ao login pode iniciar a viagem se o veículo estiver disponível, sem aprovação prévia.
- Os 3 veículos podem ser compartilhados pelos 5 técnicos; o sistema bloqueia nova retirada enquanto o veículo estiver em uso.
- Vistoria fotográfica obrigatória na retirada e na devolução: frente, traseira, lateral esquerda, lateral direita, interior e painel/quilometragem.
- As fotos ficam vinculadas ao registro da viagem e podem ser consultadas no histórico da utilização.
- Devolução com KM, horário, observações e validação da quilometragem; o veículo só volta a ficar disponível após a vistoria e o registro da devolução.
- Captura opcional de coordenadas GPS no início e no encerramento, se o técnico autorizar a localização no navegador.
- Edição dos dados originais da saída restrita ao administrador e registrada na auditoria.
- Checklist de vistoria: pneus, luzes, lataria, equipamentos, observações e foto.

### Abastecimento, manutenção e documentos
- Abastecimento com data, KM, litros, valor, combustível, posto, observações e comprovante.
- Validação de litros/valores e atualização da quilometragem do veículo quando o KM lançado for maior.
- Manutenção preventiva/corretiva, oficina, custo, status e próxima revisão por data/KM.
- Documentos com vencimento e observações; seguro também pode ser controlado no cadastro do veículo.

### Multas e relatórios
- Multas com veículo, colaborador, data, descrição, valor, vencimento, status e observações.
- Multas não canceladas incluídas nos custos e relatórios por veículo.
- Relatórios em CSV e PDF com filtro por data inicial/final; indicadores de KM, consumo estimado, combustível e utilização por colaborador.

### Usuários, segurança e auditoria
- Login individual e perfis Administrador, Responsável pela frota e Colaborador.
- Na aba Colaboradores, o administrador pode criar o colaborador e, opcionalmente, já cadastrar login e senha individual.
- Nova tela **Permissões de acesso** para habilitar ou bloquear individualmente Painel, Notificações, Veículos, Utilização, Colaboradores, Vistorias, Abastecimentos, Manutenções, Documentos, Multas, Relatórios e Indicadores avançados.
- Menus e rotas protegidos por permissão; alterações de permissões são registradas na auditoria. O administrador mantém acesso completo.
- Usuário colaborador vinculado ao cadastro de colaborador; acesso de colaborador não pode lançar retirada para outra pessoa.
- Ativação/desativação de usuários e colaboradores sem apagar histórico.
- Alteração de senha pelo próprio usuário.
- Log das ações relevantes para rastreabilidade, visível ao administrador.
- Download de backup ZIP contendo snapshot do banco SQLite e arquivos de fotos/comprovantes.

## Primeiro acesso
Usuário: `admin`  
Senha inicial: `Secw@123`

**Altere a senha inicial antes de usar com dados reais.** Para uso oficial, configure uma chave secreta exclusiva na variável de ambiente `SECFROTA_SECRET`.

## Publicação no Render
- O projeto inclui `Procfile`, `render.yaml` e Gunicorn para iniciar a aplicação no Render.
- O `render.yaml` configura um Persistent Disk em `/var/data`, usando `SECFROTA_DATA_DIR` para guardar o banco e as fotos entre reinicializações/deploys.
- Ao criar/atualizar o serviço, confirme que o plano do Render permite Persistent Disk. Sem disco persistente, SQLite e fotos podem ser perdidos em um deploy/reinício.
- Configure HTTPS (o Render fornece no domínio padrão) para permitir a geolocalização pelo navegador.
- Defina/guarde a senha de administrador e faça backup antes de migrar dados existentes.

## Execução no Windows
1. Extraia o ZIP em uma pasta no computador que ficará como servidor.
2. Execute `run_windows.bat` para uso local.
3. Abra `http://127.0.0.1:5000` no navegador.
4. Para outros computadores na mesma rede, use `run_network_windows.bat`, libere a porta TCP 5000 somente na rede privada e acesse `http://IP-DO-SERVIDOR:5000`.

Os arquivos `.bat` criam/ativam o ambiente virtual e instalam os requisitos quando houver acesso ao repositório de pacotes. Se a rede estiver sem internet, instale previamente Python 3.10+ e as dependências listadas em `requirements.txt`.

## Dados e backup
- Banco: `secw_frota.db`.
- Fotos e comprovantes: pasta `uploads/`.
- O menu **Backup do banco** baixa um ZIP com banco e uploads. Guarde o backup em local seguro e teste periodicamente a restauração.
- Não apague o banco nem a pasta `uploads/` durante atualização. A inicialização faz migrações compatíveis com os campos adicionados nesta versão.

## Observações importantes
- A estimativa de consumo km/l usa a quilometragem registrada nas viagens encerradas dividida pelos litros cadastrados; a precisão depende de lançamentos completos e consistentes.
- O sistema registra alertas no painel; ele não envia e-mails ou notificações externas automaticamente.
- O backup é acionado pelo administrador, não é uma rotina automática agendada.
- Esta versão foi validada por compilação Python e análise sintática dos modelos Jinja. O ambiente de construção não tinha Flask disponível e estava sem acesso à internet, então não foi possível executar um teste web ponta a ponta aqui. Instale os requisitos e valide o fluxo no computador da empresa antes de usar com dados reais.
- `run_network_windows.bat` é para rede interna de confiança, não para expor o sistema diretamente à internet. Para acesso externo, use servidor WSGI, HTTPS, chave secreta forte, firewall e revisão técnica.

## Perfis de acesso e identidade visual
- A tela de login usa a identidade visual SEC.W azul-marinho e azul-claro, baseada na referência fornecida.
- Na aba **Colaboradores**, o administrador pode cadastrar nome, departamento, telefone e, opcionalmente, login e senha na mesma etapa. A área **Técnicos e usuários** continua disponível para cadastro separado.
- Na área **Permissões de acesso**, selecione um usuário e marque as funções que ele poderá acessar. Novos técnicos começam com Utilização e Abastecimentos liberados; as demais áreas podem ser habilitadas pelo administrador.
- O perfil **Técnico** (internamente identificado como `employee`) acessa somente **Utilização** e **Abastecimentos**, além da opção auxiliar para alterar a própria senha e sair do sistema. Não acessa painel, notificações, vistorias, veículos, relatórios nem áreas administrativas, inclusive digitando o endereço diretamente.
- O técnico pode iniciar e devolver viagens vinculadas ao próprio cadastro; os abastecimentos lançados por ele ficam associados ao seu usuário.
- As seis imagens da vistoria de saída e as seis imagens da devolução são obrigatórias no servidor. O atributo `capture="environment"` solicita a câmera traseira nos navegadores móveis compatíveis, mas o navegador pode oferecer também a galeria; isso não pode ser bloqueado de forma confiável por um site comum.
- O GPS é capturado como ponto de saída e ponto de devolução, se autorizado. Não é acompanhamento em tempo real nem rastreamento contínuo.
- Técnicos iniciam a utilização diretamente, desde que o veículo esteja disponível e a vistoria de saída esteja completa.
- No primeiro acesso padrão: usuário `admin`, senha `Secw@123`. Altere a senha antes do uso real.


## Melhorias adicionais da versão completa
- Central de notificações internas por usuário, com avisos de solicitação, aprovação e recusa de uso do veículo.
- Campo de previsão de devolução e validação básica de conflito de agenda antes de registrar solicitações e aprová-las.
- Colaboradores podem registrar vistorias e consultar somente suas próprias vistorias; a administração continua vendo o histórico geral.
- Arquivos enviados exigem sessão autenticada para visualização.
- O relatório avançado já reúne quilômetros rodados, litros e custos de combustível, manutenção e multas por veículo.

## Segurança antes do uso real
Altere a senha inicial `Secw@123`, defina a variável de ambiente `SECFROTA_SECRET` com uma chave longa e aleatória, mantenha o sistema em rede interna protegida e faça cópias periódicas do banco `secw_frota.db` e da pasta `uploads`. O sistema cria um backup ZIP local diário no primeiro acesso de cada dia e conserva os 14 mais recentes; o backup manual também está disponível no menu administrativo. Como todo backup local, ele não protege contra perda física do próprio computador: copie periodicamente a pasta `backups` para outro dispositivo/local seguro.


## Login inicial
- Usuário: `admin`
- Senha inicial: `Secw@123`
- Troque essa senha no primeiro acesso.


## Validação desta versão
Foram verificados a sintaxe/compilação Python, a sintaxe dos modelos Jinja e o esquema SQLite, incluindo testes das consultas SQL dos relatórios. A interface completa não pôde ser executada neste ambiente porque o Flask não está instalado e não foi possível baixar dependências. Antes da entrada em produção, instale os requisitos e teste os fluxos reais da empresa.

## Integração com Google Maps / GPS

Na utilização, o navegador pode solicitar permissão para capturar as coordenadas no início e na devolução. Os pontos autorizados ficam gravados no histórico e podem ser abertos no Google Maps. A rota de destino também pode ser aberta no Google Maps. O sistema não faz rastreamento contínuo em segundo plano; para localização ao vivo durante todo o percurso, será necessária uma solução de rastreamento dedicada ou aplicativo móvel com permissões e política de privacidade apropriadas.


## Cadastro direto de técnicos
No menu do administrador, use **Cadastrar técnicos**. O formulário cria, em uma única operação, o registro do colaborador e seu usuário de login, vinculando os dois automaticamente. O perfil criado é sempre `employee` (Técnico), com acesso somente a Utilização e Abastecimentos. Para criar, é obrigatório informar nome, usuário e senha com no mínimo 8 caracteres.


## Acesso administrativo inicial
Em uma instalação sem usuário administrador, o sistema garante o perfil administrador inicial:
- **Usuário:** `admin`
- **Senha inicial:** `Secw@123`

Entre com esse perfil e acesse **Cadastrar técnicos** no menu lateral. O cadastro cria o colaborador e o login do técnico em uma única etapa. O técnico fica restrito às áreas de **Utilização** e **Abastecimentos**. Por segurança, altere a senha administrativa após o primeiro acesso.


### Despesas com pedágios
Acesse **Pedágios** no menu para registrar veículo, data, praça, trecho/sentido, valor, forma de pagamento, comprovante e observações. As despesas são exibidas na lista e somadas ao painel de despesas do mês. O acesso pode ser liberado individualmente em **Permissões de acesso**.


## Melhorias de responsividade e controle
- A interface inclui navegação móvel com menu lateral recolhível, botões com área de toque ampliada e formulários em uma coluna em telas pequenas.
- O cadastro de colaboradores aceita número, categoria e validade da CNH; o painel alerta para vencimentos em até 30 dias.
- Os relatórios financeiros incluem despesas de pedágios nos totais por veículo.
- Para uso em vários dispositivos, execute a aplicação em um computador/servidor acessível pela rede da empresa e acesse pelo IP/hostname desse servidor. A geolocalização do navegador exige HTTPS (exceto localhost) e autorização do usuário.
- Faça backup periódico pelo menu administrativo e mantenha cópia do arquivo de banco `secw_frota.db` e da pasta `uploads`.


## Como acessar pelo celular na rede da empresa
1. No computador que ficará ligado como servidor, execute `run_network_windows.bat`.
2. Conecte o celular à mesma rede Wi-Fi da empresa.
3. No computador, descubra o endereço IPv4 usando `ipconfig`.
4. No celular, abra o navegador e acesse `http://IP-DO-COMPUTADOR:5000` (substitua pelo IPv4 real).
5. Se não abrir, verifique se o Firewall do Windows permite Python/porta 5000 na rede privada. Não exponha a porta diretamente à internet.

O layout foi adaptado para telas pequenas com menu lateral recolhível, formulários em coluna única e tabelas com rolagem horizontal. A geolocalização em celular geralmente exige HTTPS; em HTTP na rede local, a captura de GPS pode não estar disponível.

## Recursos desta atualização
- Painel com indicadores da frota, despesas mensais e alertas de documentação, manutenção, multas e CNH.
- Registro de CNH (número, categoria e validade) e alertas para vencimentos em até 30 dias.
- Custos de pedágios incluídos nos relatórios gerencial, CSV e PDF, além dos indicadores de custo por quilômetro.
- Confirmação eletrônica de retirada e devolução associada ao usuário autenticado e horário, como trilha de auditoria (não substitui assinatura digital certificada).
- Cookies de sessão com HttpOnly e SameSite=Lax; HTTPS pode ser habilitado definindo `SECFROTA_HTTPS=1` atrás de um servidor HTTPS.
- Backup automático diário em ZIP (banco + fotos/comprovantes), com retenção dos 14 backups locais mais recentes.

## Checklist de equipamentos na retirada

A área **Equipamentos dos veículos** é exclusiva do administrador. Nela é possível adicionar itens, alterar seus nomes e ativar/desativar itens da lista. Ao cadastrar pelo menos um item ativo, a tela de retirada passa a exigir que o técnico marque cada equipamento como **Presente** ou **Ausente** antes de registrar a saída. Há um campo opcional de observação por item.

As conferências ficam gravadas por utilização com o nome do item, resultado e observação. Desativar ou renomear itens não modifica as conferências antigas. A lista pode começar vazia; nesse caso, o formulário de retirada permanece como antes até que o administrador cadastre os itens.

## Acompanhamento da frota por GPS (novo)

- O menu **Acompanhamento da frota** fica disponível para administrador e responsável pela frota.
- O técnico encontra **Minha localização** no menu do próprio acesso.
- O técnico precisa estar com uma utilização de veículo aprovada e ativa, tocar em **Ativar localização** e autorizar o GPS do aparelho.
- O navegador envia coordenadas enquanto a página do aplicativo está aberta e o GPS está disponível. O painel administrativo atualiza a lista e o mapa automaticamente a cada 15 segundos.
- O compartilhamento pode ser interrompido pelo próprio técnico e é encerrado automaticamente quando o sistema identifica que a utilização do veículo foi devolvida.
- O banco registra até 500 posições recentes por técnico. O acesso ao mapa e à API de monitoramento é restrito aos perfis administrador e responsável pela frota.
- O sistema também pode ser instalado como aplicativo web (PWA) pelo navegador compatível, usando HTTPS.

**Limitação importante:** esta versão usa a localização do navegador (GPS do celular). Como PWA, o Android pode suspender a localização quando o aplicativo fica em segundo plano ou a tela é bloqueada. Para rastreamento contínuo em segundo plano, será necessária uma etapa nativa Android (por exemplo, aplicativo Android com serviço de localização em primeiro plano e notificação persistente), além da publicação/instalação do APK. O mapa usa dados do OpenStreetMap via internet.

---

## Versão evolutiva — 06/11/2026

Esta edição preserva o banco de dados existente e adiciona uma camada visual renovada, cadastro de fotos dos colaboradores e regra de compartilhamento de veículos sob responsabilidade diária.

### O que foi incluído nesta entrega
- Identidade visual revisada, com dashboard, painéis, tabelas e formulários mais limpos e responsivos para computador e Android.
- Tela de login centralizada.
- Upload de foto do colaborador no cadastro e atualização de foto de colaboradores já cadastrados.
- Foto do colaborador exibida na listagem e ao lado do nome no histórico de utilização.
- Compartilhamento: um técnico pode registrar utilização avulsa de um veículo que esteja sob responsabilidade diária de outro técnico.
- O responsável diário e os perfis administrador/responsável pela frota recebem notificação interna quando ocorre o compartilhamento e quando o veículo é devolvido.
- Bloqueio de uma segunda utilização avulsa simultânea do mesmo veículo.
- Ao devolver a utilização avulsa, o veículo continua marcado como "Em uso" enquanto existir uma responsabilidade diária ativa.
- Migração automática e compatível para adicionar o campo de foto na tabela de colaboradores; os registros existentes são mantidos.

### Recursos que dependem de configuração externa antes de serem considerados ativos
- **Push Android com aplicativo fechado:** esta entrega mantém as notificações internas do sistema. Push real exige assinatura Web Push, chaves VAPID, HTTPS e configuração do navegador/aparelho. Não habilite essa função como concluída sem configurar e testar as chaves e a inscrição dos dispositivos.
- **Localização em segundo plano:** o consentimento e a sessão de localização já existentes são mantidos. O navegador/PWA pode suspender atualizações quando fechado; rastreamento contínuo confiável em segundo plano no Android exige aplicativo Android nativo/serviço em primeiro plano e permissões apropriadas. A permissão do navegador costuma persistir após a escolha do usuário, mas o sistema operacional pode redefini-la.

### Atualização segura
1. Faça backup pelo menu **Backup do banco** antes de publicar.
2. Substitua os arquivos da aplicação pelos desta versão, mantendo o banco persistente e a pasta `uploads`.
3. Não apague o disco persistente do Render nem o arquivo `secw_frota.db`.
4. Publique e teste primeiro com um administrador e dois técnicos de teste.
5. Confira retirada, devolução, fotos, compartilhamento de um veículo com responsabilidade diária e notificações internas.

### Testes desta compilação
- Verificação de sintaxe Python (`py_compile`) concluída.
- Integridade do arquivo ZIP verificada na geração.
- Teste funcional Flask não foi executado neste ambiente porque a dependência Flask não está instalada aqui. Execute os testes de fluxo em ambiente com `requirements.txt` instalado antes de colocar em produção.

## PostgreSQL no Render

O sistema usa PostgreSQL quando a variável de ambiente `DATABASE_URL` está configurada. No Render, configure essa variável no serviço Web com a **Internal Database URL** do banco PostgreSQL já criado. Mantenha o Persistent Disk para armazenar fotos e comprovantes (`SECFROTA_DATA_DIR=/var/data`). Se `DATABASE_URL` não estiver configurada, o sistema continua usando SQLite em instalações locais.

### Migração dos dados SQLite existentes

A troca do banco não apaga o SQLite antigo, mas os registros que já estavam nele não aparecem automaticamente no PostgreSQL. Se o SQLite estiver no disco persistente do Render, publique esta versão mantendo o mesmo disco e a variável `DATABASE_URL`; depois, no **Shell** do serviço Web no Render, execute:

```bash
python migrar_sqlite_para_postgres.py /var/data/secw_frota.db
```

O script cria a estrutura no PostgreSQL, importa os registros e mantém os arquivos da pasta `/var/data/uploads`. Execute uma única vez e somente se o PostgreSQL estiver novo/vazio. Faça backup antes da migração. Se o SQLite estiver apenas em um backup ZIP, o script também aceita o caminho desse ZIP.

## Correções e melhorias — atualização 13/11/2026
- Abastecimento: atualização da quilometragem compatível com SQLite e PostgreSQL, corrigindo a expressão de maior valor que podia falhar no PostgreSQL durante a gravação.
- Utilização: os campos preenchidos ficam preservados no navegador após retorno por validação. Por segurança do navegador, as fotografias selecionadas precisam ser escolhidas novamente se o servidor rejeitar o formulário.
- Notificações: o técnico responsável pela responsabilidade diária recebe aviso quando outro técnico registra uma utilização avulsa no veículo atribuído; a administração e a frota também são avisadas.
- Foto do perfil: o próprio técnico pode enviar/atualizar sua foto na tela inicial, além da atualização administrativa já existente.
- Tela inicial do técnico: após o login, o técnico vê sua foto, nome, situação da CNH, número/categoria/validade, alerta de vencimento, utilização ativa, atalhos e notificações recentes.
- A situação da CNH é calculada pela data cadastrada. Se a validade não estiver preenchida, o sistema informa que falta cadastro; não presume que a CNH esteja regular.
