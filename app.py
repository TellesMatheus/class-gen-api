import logging
from flask_cors import CORS
from flask import Flask, request, send_file
from flask_restx import Api, Resource
import pandas as pd
import numpy as np
from deap import base, creator, tools, algorithms
import os
from werkzeug.datastructures import FileStorage
import matplotlib.pyplot as plt

# Configuração de Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app)  # Adiciona suporte ao CORS
api = Api(app, version='1.0', title='API de Distribuição de Horários',
          description='Uma API para distribuir horários e salas.')

app.logger.setLevel(logging.INFO)

# Configuração do DEAP
if 'FitnessMax' in creator.__dict__:
    del creator.FitnessMax
if 'Individual' in creator.__dict__:
    del creator.Individual

creator.create("FitnessMax", base.Fitness, weights=(1.0,))
creator.create("Individuo", list, fitness=creator.FitnessMax)

toolbox = base.Toolbox()

# Namespace para upload
ns = api.namespace('upload', description='Operações de Upload')

upload_parser = api.parser()
upload_parser.add_argument('file', location='files',
                           type=FileStorage, required=True, help='Arquivo Excel')
upload_parser.add_argument('distributeRooms', type=str, required=True,
                           location='form', help='Distribuir Salas (true ou false)')
upload_parser.add_argument('geneticLevel', type=str, required=True,
                           location='form', help='Nível do operador genético (Razoável, Normal, Bom)')


@ns.route('/')
class UploadFile(Resource):
    @ns.expect(upload_parser)
    def post(self):
        args = upload_parser.parse_args()
        arquivo = args['file']
        distribuir_salas = args['distributeRooms'].lower() == 'true'
        nivel_genetico = args['geneticLevel'].lower()

        # Configurações de operadores genéticos baseados no nível
        configuracoes_geneticas = {
            'razoável': {'cxpb': 0.5, 'mutpb': 0.1, 'ngen': 50, 'tamanho_populacao': 100},
            'normal': {'cxpb': 0.8, 'mutpb': 0.2, 'ngen': 100, 'tamanho_populacao': 200},
            'bom': {'cxpb': 0.95, 'mutpb': 0.3, 'ngen': 200, 'tamanho_populacao': 300}
        }

        # Se o nível genético não for reconhecido, use as configurações normais por padrão
        cxpb, mutpb, ngen, tamanho_populacao = configuracoes_geneticas.get(
            nivel_genetico, configuracoes_geneticas['normal']).values()
        logging.info(f"Nível Genético: {nivel_genetico}, CXPB: {cxpb}, MUTPB: {mutpb}, NGEN: {ngen}, Tamanho da População: {tamanho_populacao}")

        try:
            dados = pd.read_excel(arquivo, dtype=str)

            valido, erros = validar_componentes_sem_professor(dados)
            if not valido:
                return {"message": f"Erro de validação: {', '.join(erros)}"}, 400
            
            semestre = dados.iloc[0]['Semestre']
            salas_disponiveis = dados.iloc[0]['Sala'].split(', ')

            dados_saida = processar_dados(
                dados, salas_disponiveis, distribuir_salas, semestre, cxpb, mutpb, ngen, tamanho_populacao)
            df_saida = pd.DataFrame(dados_saida, columns=[
                                    'Professor', 'Dia da Aula', 'Componente', 'Sala', 'Semestre'])

            caminho_saida = os.path.join(
                os.getcwd(), 'horarios_otimizados_saida.xlsx')

            with pd.ExcelWriter(caminho_saida, engine='xlsxwriter') as writer:
                df_saida.to_excel(writer, index=False, sheet_name='Sheet1')
                workbook = writer.book
                worksheet = writer.sheets['Sheet1']

                formato_vermelho = workbook.add_format({'bg_color': 'red', 'font_color': 'black'})
                
                # Definir o formato das colunas
                num_colunas = len(df_saida.columns)
                worksheet.set_column(0, num_colunas - 1, 20)  # Ajuste o tamanho das colunas conforme necessário
                
                # Aplica a formatação condicional
                for idx in range(len(df_saida)):
                    dia_vazio = pd.isna(df_saida.loc[idx, 'Dia da Aula']) or df_saida.loc[idx, 'Dia da Aula'].strip() == ''
                    sala_vazia = pd.isna(df_saida.loc[idx, 'Sala']) or df_saida.loc[idx, 'Sala'].strip() == ''
                    componente = df_saida.loc[idx, 'Componente']
                    professor = df_saida.loc[idx, 'Professor']

                    # Verifica se é um componente MIX
                    if "_" in componente:
                        dia_mix = int(componente.split('_')[1])
                        dias_disponiveis_professor = dados[dados['Professor'] == professor]['Disponibilidade do Professor'].values[0].split(', ')
                        dias_semana = {1: 'Segunda', 2: 'Terça', 3: 'Quarta', 4: 'Quinta', 5: 'Sexta', 6: 'Sábado'}
                        dia_especifico = dias_semana.get(dia_mix, '')

                        # Verifica se o dia mix está disponível para o professor
                        if dia_especifico not in dias_disponiveis_professor:
                            worksheet.write_row(idx + 1, 0, df_saida.iloc[idx].values, formato_vermelho)
                    else:
                        if distribuir_salas:
                            if dia_vazio or sala_vazia:
                                worksheet.write_row(idx + 1, 0, df_saida.iloc[idx].values, formato_vermelho)
                        else:
                            if dia_vazio:
                                worksheet.write_row(idx + 1, 0, df_saida.iloc[idx].values, formato_vermelho)

            if os.path.exists(caminho_saida):
                return send_file(caminho_saida, as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            else:
                return {"message": "Erro ao salvar o arquivo"}, 500
        except Exception as e:
            return {"message": f"Erro ao processar o arquivo: {str(e)}"}, 500
        
def validar_componentes_sem_professor(dados):
    """Valida se há componentes sem professor alocado."""
    erros = []
    for idx, row in dados.iterrows():
        professor = str(row['Professor']).strip() if isinstance(row['Professor'], str) else ''
        componente = str(row['Componente']).strip() if isinstance(row['Componente'], str) else ''
        if not professor and componente:
            erros.append(f"Linha {idx+1}: Componente '{componente}' sem professor.")
    
    if erros:
        return False, erros
    return True, None

# Método para processar os dados e executar o algoritmo genético
def processar_dados(dados, salas_disponiveis, distribuir_salas, semestre, cxpb, mutpb, ngen, tamanho_populacao):
    toolbox.register("individual", tools.initIterate,
                     creator.Individuo, criar_individuo(dados))
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("evaluate", avaliar_horario)
    toolbox.register("mate", tools.cxUniform, indpb=0.5)
    toolbox.register("mutate", mutacao, dados=dados)
    toolbox.register("select", tools.selTournament, tournsize=3)

    populacao = toolbox.population(n=tamanho_populacao)
    
    # Coletar métricas durante o algoritmo genético
    resultado, medias = coletar_metricas(populacao, toolbox, cxpb, mutpb, ngen)

    melhor_individuo = tools.selBest(resultado, k=1)[0]
    dados_saida = [[prof, dia, comp, '', semestre]
                   for prof, dia, comp in melhor_individuo]

    if distribuir_salas:
        distribuir_salas_func(dados_saida, salas_disponiveis)

    return dados_saida

def coletar_metricas(populacao, toolbox, cxpb, mutpb, ngen):
    """Coleta métricas e gera gráfico de desempenho do algoritmo genético."""
    
    # Lista para armazenar as médias de fitness
    medias = []

    logging.info(f"Inicializando AG com CXPB: {cxpb}, MUTPB: {mutpb}, NGEN: {ngen}")

    # Algoritmo genético com coleta de métricas
    for gen in range(ngen):
        # Avaliar todos os indivíduos da população
        fits = list(map(toolbox.evaluate, populacao))
        for ind, fit in zip(populacao, fits):
            ind.fitness.values = fit
        
        # Coletar a média de fitness
        media_fitness = np.mean([fit[0] for fit in fits])
        
        medias.append(media_fitness)  # Média da população
        
        # Log da média no console
        logging.info(f"Geração {gen+1}: Média Fitness: {media_fitness}")

        # Seleção, cruzamento e mutação
        populacao = toolbox.select(populacao, len(populacao))
        populacao = list(map(toolbox.clone, populacao))
        offspring = list(map(toolbox.clone, populacao))
        
        for child1, child2 in zip(offspring[::2], offspring[1::2]):
            if np.random.rand() < cxpb:
                toolbox.mate(child1, child2)
                del child1.fitness.values
                del child2.fitness.values
        
        for mutant in offspring:
            if np.random.rand() < mutpb:
                toolbox.mutate(mutant)
                del mutant.fitness.values

        populacao[:] = offspring

    # Após todas as gerações
    logging.info("Finalizando métricas do AG")

    # Gera o gráfico de desempenho (apenas com a média)
    gerar_grafico(medias)

    return populacao, medias


# Função para gerar gráfico de métricas (apenas com a média de fitness)
def gerar_grafico(medias):
    plt.figure(figsize=(10, 6))

    # Linha da média de fitness
    plt.plot(medias, label="Média Fitness", color="green", linestyle="--", marker="o")

    # Limites para o eixo Y para melhorar a visualização
    plt.ylim([min(medias) * 0.9, max(medias) * 1.1])

    # Labels e título
    plt.xlabel("Geração")
    plt.ylabel("Pontuação Fitness")
    plt.title("Desempenho do Algoritmo Genético (Média de Fitness)")
    
    # Legenda
    plt.legend()
    
    # Grade
    plt.grid(True)
    
    # Exibe o gráfico
    plt.show()


# Demais métodos auxiliares como mutação, avaliação, distribuição de salas e criação do indivíduo
def criar_individuo(dados):
    def inner():
        individuo = []
        professor_dias_atribuidos = {linha['Professor']: set()
                                      for _, linha in dados.iterrows() if linha['Professor'] != 'Geral'}
        dias_semana = {1: 'Segunda', 2: 'Terça', 3: 'Quarta', 4: 'Quinta', 5: 'Sexta', 6: 'Sábado'}
        
        for _, linha in dados.iterrows():
            if linha['Professor'] == 'Geral':
                continue
            professor = linha['Professor']
            dias_disponiveis = set(
                linha['Disponibilidade do Professor'].split(', '))
            dias_atribuidos = professor_dias_atribuidos[professor]
            componentes = linha['Componente'].split(',')
            dias_validos = list(dias_disponiveis - dias_atribuidos)
            
            for componente in componentes:
                if componente.strip():
                    if "_" in componente:
                        dia_mix = int(componente.split('_')[1])
                        dia_especifico = dias_semana.get(dia_mix, '')
                        individuo.append((professor, dia_especifico, componente.strip()))
                        dias_atribuidos.add(dia_especifico)
                    elif dias_validos:
                        dia = np.random.choice(dias_validos)
                        dias_atribuidos.add(dia)
                        individuo.append((professor, dia, componente.strip()))
                        dias_validos.remove(dia)
                    else:
                        individuo.append((professor, '', componente.strip()))
        return individuo
    return inner

def mutacao(individuo, dados):
    for idx in range(len(individuo)):
        if len(individuo[idx]) == 3:
            professor, dia, componente = individuo[idx]
            dias_disponiveis = set(dados[dados['Professor'] == professor]
                                   ['Disponibilidade do Professor'].values[0].split(', '))
            dias_disponiveis.discard(dia)
            if dias_disponiveis:
                novo_dia = np.random.choice(list(dias_disponiveis))
                individuo[idx] = (professor, novo_dia, componente)
    return individuo,

def avaliar_horario(individuo):
    pontuacao = 0
    uso_dias_professor = {}
    for professor, dia, componente in individuo:
        if len((professor, dia, componente)) == 3:
            if dia in uso_dias_professor.get(professor, set()):
                continue
            uso_dias_professor.setdefault(professor, set()).add(dia)
            pontuacao += 1
    return (pontuacao,)

def distribuir_salas_func(dados_saida, salas_disponiveis):
    uso_salas_dia = {dia: salas_disponiveis.copy()
                     for dia in set(entrada[1] for entrada in dados_saida)}
    for entrada in dados_saida:
        dia = entrada[1]
        if dia:  # Verifica se o dia não está vazio
            if uso_salas_dia.get(dia):
                if uso_salas_dia[dia]:
                    sala = uso_salas_dia[dia].pop(0)
                    entrada[3] = sala
                else:
                    entrada[3] = ''  # Marca a sala como vazia se não houver sala disponível
        else:
            entrada[3] = ''  # Marca a sala como vazia se o dia não estiver definido


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
