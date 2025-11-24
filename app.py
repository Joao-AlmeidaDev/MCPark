import os
import sys
from datetime import datetime, timedelta
from functools import wraps, lru_cache
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import pandas as pd
import numpy as np
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import FlaskForm
from wtforms import (StringField, PasswordField, SubmitField, TextAreaField, 
                    SelectField, DateField, DecimalField, IntegerField, HiddenField)
from wtforms.validators import DataRequired, Email, Optional, NumberRange, Length
from dateutil.relativedelta import relativedelta
import time
from threading import Lock

# --- Configuração do Aplicativo ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'sua-chave-secreta-aqui'
app.config['DATA_DIR'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# Configuração do Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Constantes
DATA_DIR = app.config['DATA_DIR']

# Sistema de cache global
CSV_CACHE = {}
CACHE_LOCK = Lock()
CACHE_TIMEOUT = 30  # Cache expira em 30 segundos
CACHE_TIMESTAMPS = {}

# --- Filtros Jinja2 ---
@app.template_filter('format_cpf')
def format_cpf_filter(cpf):
    """Formata CPF: 123.456.789-00"""
    if not cpf:
        return '-'
    cpf = ''.join(filter(str.isdigit, str(cpf)))
    if len(cpf) == 11:
        return f'{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}'
    return cpf

@app.template_filter('format_phone')
def format_phone_filter(phone):
    """Formata telefone: (11) 98765-4321"""
    if not phone:
        return '-'
    phone = ''.join(filter(str.isdigit, str(phone)))
    if len(phone) == 11:
        return f'({phone[:2]}) {phone[2:7]}-{phone[7:]}'
    elif len(phone) == 10:
        return f'({phone[:2]}) {phone[2:6]}-{phone[6:]}'
    return phone

@app.template_filter('format_cep')
def format_cep_filter(cep):
    """Formata CEP: 12345-678"""
    if not cep:
        return '-'
    cep = ''.join(filter(str.isdigit, str(cep)))
    if len(cep) == 8:
        return f'{cep[:5]}-{cep[5:]}'
    return cep

@app.template_filter('format_currency')
def format_currency_filter(value):
    """Formata valor monetário: R$ 1.234,56"""
    if value is None:
        return 'R$ 0,00'
    try:
        return f'R$ {float(value):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return 'R$ 0,00'

@app.template_filter('format_date')
def format_date_filter(date):
    """Formata data: 20/11/2025"""
    if not date:
        return '-'
    if isinstance(date, str):
        try:
            date = pd.to_datetime(date)
        except:
            return date
    return date.strftime('%d/%m/%Y') if hasattr(date, 'strftime') else str(date)

@app.template_filter('format_datetime')
def format_datetime_filter(datetime_obj):
    """Formata data e hora: 20/11/2025 14:30"""
    if not datetime_obj:
        return '-'
    if isinstance(datetime_obj, str):
        try:
            datetime_obj = pd.to_datetime(datetime_obj)
        except:
            return datetime_obj
    return datetime_obj.strftime('%d/%m/%Y %H:%M') if hasattr(datetime_obj, 'strftime') else str(datetime_obj)

# --- Funções de Apoio ---
def format_currency(value):
    return f'R$ {value:,.2f}'.replace('.', '|').replace(',', '.').replace('|', ',')

def read_csv_cached(filename, force_reload=False):
    """Lê arquivo CSV com cache para melhorar performance"""
    global CSV_CACHE, CACHE_TIMESTAMPS
    
    filepath = os.path.join(DATA_DIR, filename)
    current_time = time.time()
    
    with CACHE_LOCK:
        # Verifica se o cache existe e não expirou
        if not force_reload and filename in CSV_CACHE:
            cache_age = current_time - CACHE_TIMESTAMPS.get(filename, 0)
            if cache_age < CACHE_TIMEOUT:
                return CSV_CACHE[filename].copy()
        
        # Lê o arquivo e armazena no cache
        try:
            df = pd.read_csv(filepath)
            # Otimiza tipos de dados para reduzir memória
            for col in df.columns:
                if df[col].dtype == 'float64':
                    df[col] = pd.to_numeric(df[col], downcast='float', errors='ignore')
                elif df[col].dtype == 'int64':
                    df[col] = pd.to_numeric(df[col], downcast='integer', errors='ignore')
            
            CSV_CACHE[filename] = df
            CACHE_TIMESTAMPS[filename] = current_time
            return df.copy()
        except FileNotFoundError:
            return pd.DataFrame()

def invalidate_cache(filename=None):
    """Invalida o cache de um arquivo específico ou de todos"""
    global CSV_CACHE, CACHE_TIMESTAMPS
    with CACHE_LOCK:
        if filename:
            CSV_CACHE.pop(filename, None)
            CACHE_TIMESTAMPS.pop(filename, None)
        else:
            CSV_CACHE.clear()
            CACHE_TIMESTAMPS.clear()

def save_csv_and_invalidate(df, filename):
    """Salva CSV e invalida o cache"""
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath, index=False)
    invalidate_cache(filename)

def get_next_id(filename):
    try:
        df = read_csv_cached(filename)
        return int(df['id'].max() + 1) if not df.empty else 1
    except (KeyError, ValueError):
        return 1

# --- Funções de Decorador ---
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Acesso de administrador necessário para esta página.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# --- Formulários ---
class LoginForm(FlaskForm):
    username = StringField('Usuário', validators=[DataRequired()])
    password = PasswordField('Senha', validators=[DataRequired()])
    submit = SubmitField('Entrar')

class CustomerForm(FlaskForm):
    id = HiddenField()
    name = StringField('Nome Completo', validators=[DataRequired(), Length(max=100)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=100)])
    phone = StringField('Telefone', validators=[DataRequired(), Length(max=20)])
    phone2 = StringField('Telefone 2', validators=[Optional(), Length(max=20)])
    cpf = StringField('CPF', validators=[DataRequired(), Length(min=11, max=14)])
    rg = StringField('RG', validators=[Optional(), Length(max=20)])
    birth_date = DateField('Data de Nascimento', format='%Y-%m-%d', validators=[Optional()])
    cep = StringField('CEP', validators=[Optional(), Length(max=9)])
    street = StringField('Rua', validators=[Optional(), Length(max=100)])
    number = StringField('Número', validators=[Optional(), Length(max=10)])
    complement = StringField('Complemento', validators=[Optional(), Length(max=50)])
    neighborhood = StringField('Bairro', validators=[Optional(), Length(max=100)])
    city = StringField('Cidade', validators=[Optional(), Length(max=100)])
    state = SelectField('Estado', choices=[
        ('', 'Selecione'), ('AC','AC'), ('AL','AL'), ('AP','AP'), ('AM','AM'), ('BA','BA'), ('CE','CE'),
        ('DF','DF'), ('ES','ES'), ('GO','GO'), ('MA','MA'), ('MT','MT'), ('MS','MS'), ('MG','MG'),
        ('PA','PA'), ('PB','PB'), ('PR','PR'), ('PE','PE'), ('PI','PI'), ('RJ','RJ'), ('RN','RN'),
        ('RS','RS'), ('RO','RO'), ('RR','RR'), ('SC','SC'), ('SP','SP'), ('SE','SE'), ('TO','TO')
    ], validators=[Optional()])
    address = TextAreaField('Endereço', validators=[Optional(), Length(max=200)])
    notes = TextAreaField('Observações', validators=[Optional(), Length(max=500)])
    status = SelectField('Status', choices=[('ativo','Ativo'), ('inativo','Inativo'), ('inadimplente','Inadimplente')], validators=[Optional()])
    submit = SubmitField('Salvar')

class VehicleForm(FlaskForm):
    id = HiddenField()
    customer_id = SelectField('Cliente', coerce=int, validators=[DataRequired()])
    plate = StringField('Placa', validators=[DataRequired(), Length(min=7, max=8)])
    brand = StringField('Marca', validators=[Optional(), Length(max=50)])
    model = StringField('Modelo', validators=[DataRequired(), Length(max=50)])
    color = HiddenField()
    color_name = StringField('Cor', validators=[DataRequired(), Length(max=30)])
    year = IntegerField('Ano', validators=[Optional(), NumberRange(min=1900, max=2100)])
    type = SelectField('Tipo', choices=[('carro','Carro'), ('moto','Moto'), ('utilitario','Utilitário')], validators=[Optional()])
    renavam = StringField('RENAVAM', validators=[Optional(), Length(max=20)])
    chassis = StringField('Chassi', validators=[Optional(), Length(max=30)])
    notes = TextAreaField('Observações', validators=[Optional(), Length(max=500)])
    status = SelectField('Status', choices=[('ativo','Ativo'), ('inativo','Inativo')], validators=[Optional()])
    photos = HiddenField()
    submit = SubmitField('Salvar')

class PlanForm(FlaskForm):
    id = HiddenField()
    name = StringField('Nome do Plano', validators=[DataRequired(), Length(max=50)])
    description = TextAreaField('Descrição', validators=[Optional()])
    price = DecimalField('Preço Mensal', validators=[DataRequired(), NumberRange(min=0)])
    duration_days = IntegerField('Duração (dias)', validators=[DataRequired(), NumberRange(min=1)])
    submit = SubmitField('Salvar')

class SubscriptionForm(FlaskForm):
    customer_id = SelectField('Cliente', coerce=int, validators=[DataRequired()])
    vehicle_id = SelectField('Veículo', coerce=int, validators=[DataRequired()])
    plan_id = SelectField('Plano', coerce=int, validators=[DataRequired()])
    start_date = DateField('Data de Início', format='%Y-%m-%d', validators=[DataRequired()])
    submit = SubmitField('Ativar Assinatura')

class PaymentForm(FlaskForm):
    subscription_id = HiddenField()
    amount = DecimalField('Valor', validators=[DataRequired(), NumberRange(min=0.01)])
    payment_date = DateField('Data do Pagamento', format='%Y-%m-%d', default=datetime.today)
    payment_method = SelectField('Método de Pagamento', 
                               choices=[('dinheiro', 'Dinheiro'), 
                                       ('pix', 'PIX'),
                                       ('cartao_credito', 'Cartão de Crédito'),
                                       ('cartao_debito', 'Cartão de Débito'),
                                       ('transferencia', 'Transferência Bancária')],
                               validators=[DataRequired()])
    submit = SubmitField('Registrar Pagamento')

class FinancialTransactionForm(FlaskForm):
    description = StringField('Descrição', validators=[DataRequired(), Length(max=200)])
    amount = DecimalField('Valor', validators=[DataRequired(), NumberRange(min=0.01)])
    transaction_date = DateField('Data', format='%Y-%m-%d', default=datetime.today)
    category = SelectField('Categoria', 
                         choices=[
                             ('aluguel', 'Aluguel'),
                             ('agua', 'Água'),
                             ('luz', 'Energia Elétrica'),
                             ('funcionarios', 'Folha de Pagamento'),
                             ('manutencao', 'Manutenção'),
                             ('limpeza', 'Limpeza'),
                             ('outros', 'Outros')
                         ],
                         validators=[DataRequired()])
    type = SelectField('Tipo', 
                      choices=[('receita', 'Receita'), ('despesa', 'Despesa')],
                      validators=[DataRequired()])
    submit = SubmitField('Salvar')

# --- Modelos de Dados ---
class User(UserMixin):
    def __init__(self, id, username, role, name=''):
        self.id = id
        self.username = username
        self.role = role
        self.name = name

    def get_id(self):
        return str(self.id)

def get_user_by_id(user_id):
    try:
        users_df = read_csv_cached('users.csv')
        if users_df.empty:
            return None
        # Usar loc é mais rápido que filtragem booleana para um único resultado
        user_data = users_df.loc[users_df['id'] == int(user_id)]
        if user_data.empty:
            return None
        user_data = user_data.iloc[0]
        return User(
            id=int(user_data['id']), 
            username=user_data['username'], 
            role=user_data['role'],
            name=user_data.get('name', '')
        )
    except (IndexError, KeyError, ValueError):
        return None

@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(user_id)

def get_customer_by_id(customer_id):
    try:
        customers_df = read_csv_cached('customers.csv')
        if customers_df.empty:
            return None
        result = customers_df.loc[customers_df['id'] == int(customer_id)]
        return result.iloc[0].to_dict() if not result.empty else None
    except (IndexError, KeyError, ValueError):
        return None

def get_vehicle_by_id(vehicle_id):
    try:
        vehicles_df = read_csv_cached('vehicles.csv')
        if vehicles_df.empty:
            return None
        result = vehicles_df.loc[vehicles_df['id'] == int(vehicle_id)]
        return result.iloc[0].to_dict() if not result.empty else None
    except (IndexError, KeyError, ValueError):
        return None

def get_plan_by_id(plan_id):
    try:
        plans_df = read_csv_cached('plans.csv')
        if plans_df.empty:
            return None
        result = plans_df.loc[plans_df['id'] == int(plan_id)]
        return result.iloc[0].to_dict() if not result.empty else None
    except (IndexError, KeyError, ValueError):
        return None

def get_subscription_by_id(subscription_id):
    try:
        subs_df = read_csv_cached('subscriptions.csv')
        if subs_df.empty:
            return None
        result = subs_df.loc[subs_df['id'] == int(subscription_id)]
        return result.iloc[0].to_dict() if not result.empty else None
    except (IndexError, KeyError, ValueError):
        return None

def get_financial_summary():
    """Retorna um resumo financeiro para o dashboard"""
    summary = {
        'receita_mensal': 0,
        'despesa_mensal': 0,
        'saldo_atual': 0,
        'assinaturas_ativas': 0,
        'pagamentos_pendentes': 0
    }
    
    try:
        # Cálculo de receitas e despesas do mês atual
        transactions_df = read_csv_cached('financial_transactions.csv')
        if not transactions_df.empty:
            transactions_df['date'] = pd.to_datetime(transactions_df['date'], errors='coerce')
            
            current_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # Filtragem eficiente
            monthly_transactions = transactions_df[transactions_df['date'] >= current_month]
            
            if not monthly_transactions.empty:
                # Usar groupby é mais eficiente que filtros múltiplos
                grouped = monthly_transactions.groupby('type')['amount'].sum()
                summary['receita_mensal'] = float(grouped.get('receita', 0))
                summary['despesa_mensal'] = float(grouped.get('despesa', 0))
        
        # Cálculo do saldo atual
        summary['saldo_atual'] = summary['receita_mensal'] - summary['despesa_mensal']
        
        # Contagem de assinaturas ativas
        subs_df = read_csv_cached('subscriptions.csv')
        if not subs_df.empty:
            subs_df['end_date'] = pd.to_datetime(subs_df['end_date'], errors='coerce')
            now = datetime.now()
            summary['assinaturas_ativas'] = int((subs_df['end_date'] >= now).sum())
        
        # Contagem de pagamentos pendentes
        payments_df = read_csv_cached('payments.csv')
        if not payments_df.empty:
            summary['pagamentos_pendentes'] = int((payments_df['status'] == 'pendente').sum())
            
    except Exception:
        pass
        
    return summary

# --- Rotas Principais ---
@app.route('/')
@login_required
def index():
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    else:
        return redirect(url_for('customer_dashboard'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    financial_summary = get_financial_summary()
    
    # Carregar todos os DataFrames uma única vez
    transactions_df = read_csv_cached('financial_transactions.csv')
    customers_df = read_csv_cached('customers.csv')
    subs_df = read_csv_cached('subscriptions.csv')
    vehicles_df = read_csv_cached('vehicles.csv')
    payments_df = read_csv_cached('payments.csv')
    plans_df = read_csv_cached('plans.csv')
    
    # Últimas transações com nomes de clientes
    transactions = []
    if not transactions_df.empty:
        transactions_df['date'] = pd.to_datetime(transactions_df['date'], errors='coerce')
        transactions_df = transactions_df.sort_values('date', ascending=False).head(5)
        
        # Merge com clientes de uma vez (mais eficiente que loops)
        if not customers_df.empty and not subs_df.empty:
            for _, trans in transactions_df.iterrows():
                trans_dict = trans.to_dict()
                if pd.notna(trans_dict.get('related_id')):
                    try:
                        sub = subs_df.loc[subs_df['id'] == trans_dict['related_id']]
                        if not sub.empty:
                            customer_id = sub.iloc[0]['customer_id']
                            customer = customers_df.loc[customers_df['id'] == customer_id]
                            if not customer.empty:
                                trans_dict['customer_name'] = customer.iloc[0]['name']
                    except:
                        pass
                transactions.append(trans_dict)
        else:
            transactions = transactions_df.to_dict('records')
    
    # Próximos vencimentos
    upcoming_renewals = []
    if not subs_df.empty:
        subs_df_sorted = subs_df.sort_values('end_date').head(5)
        upcoming_renewals = subs_df_sorted.to_dict('records')
    
    # Clientes ativos
    active_customers = len(customers_df) if not customers_df.empty else 0
    
    # Total de veículos cadastrados
    total_vehicles = len(vehicles_df) if not vehicles_df.empty else 0
    
    # Receita mensal
    monthly_revenue = float(financial_summary.get('receita_mensal', 0) or 0)
    
    # Vencimentos hoje
    due_today = 0
    if not subs_df.empty:
        subs_df['end_date'] = pd.to_datetime(subs_df['end_date'], errors='coerce')
        today_date = datetime.now().date()
        due_today = int((subs_df['end_date'].dt.date == today_date).sum())
    
    # Inadimplentes
    overdue_count = 0
    if not payments_df.empty:
        payments_df['payment_date'] = pd.to_datetime(payments_df['payment_date'], errors='coerce')
        overdue_mask = (payments_df['status'] == 'pendente') & (payments_df['payment_date'].dt.date < datetime.now().date())
        overdue_count = int(overdue_mask.sum())
    
    # Dados para o gráfico financeiro (últimos 6 meses) - otimizado
    financial_chart_data = {'labels': [], 'receitas': [], 'despesas': []}
    if not transactions_df.empty:
        today = datetime.now()
        for i in range(5, -1, -1):
            month_date = today - relativedelta(months=i)
            month_start = month_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if i == 0:
                month_end = today
            else:
                month_end = (month_start + relativedelta(months=1)) - timedelta(days=1)
            
            # Filtra transações do mês
            month_transactions = transactions_df[
                (transactions_df['date'] >= month_start) & 
                (transactions_df['date'] <= month_end)
            ]
            
            # Usa groupby para cálculo eficiente
            if not month_transactions.empty:
                grouped = month_transactions.groupby('type')['amount'].sum()
                receitas = float(grouped.get('receita', 0))
                despesas = float(grouped.get('despesa', 0))
            else:
                receitas = 0
                despesas = 0
            
            financial_chart_data['labels'].append(month_start.strftime('%b'))
            financial_chart_data['receitas'].append(receitas)
            financial_chart_data['despesas'].append(despesas)
    else:
        financial_chart_data = {
            'labels': ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun'],
            'receitas': [0, 0, 0, 0, 0, 0],
            'despesas': [0, 0, 0, 0, 0, 0]
        }
    
    # Dados para o gráfico de planos - otimizado
    plans_chart_data = {'labels': [], 'values': []}
    if not subs_df.empty and not plans_df.empty:
        subs_df['end_date'] = pd.to_datetime(subs_df['end_date'], errors='coerce')
        now = datetime.now()
        active_subs = subs_df[subs_df['end_date'] >= now]
        
        if not active_subs.empty:
            # Usa value_counts para contagem eficiente
            plan_counts = active_subs['plan_id'].value_counts()
            
            # Merge com planos para obter nomes
            for plan_id, count in plan_counts.items():
                plan = plans_df.loc[plans_df['id'] == plan_id]
                if not plan.empty:
                    plan_name = plan.iloc[0]['name']
                    plans_chart_data['labels'].append(plan_name)
                    plans_chart_data['values'].append(int(count))
    
    if not plans_chart_data['labels']:
        plans_chart_data = {'labels': ['Sem dados'], 'values': [1]}
    
    # Listas para os modais
    customers_list = customers_df.to_dict('records') if not customers_df.empty else []
    plans_list = plans_df[plans_df['is_active'] == True].to_dict('records') if not plans_df.empty else []
    
    return render_template('admin/dashboard.html', 
                         financial_summary=financial_summary,
                         transactions=transactions,
                         recent_transactions=transactions,
                         upcoming_renewals=upcoming_renewals,
                         active_customers=active_customers,
                         monthly_revenue=monthly_revenue,
                         due_today=due_today,
                         financial_chart_data=financial_chart_data,
                         plans_chart_data=plans_chart_data,
                         total_vehicles=total_vehicles,
                         overdue_count=overdue_count,
                         customers=customers_list,
                         plans=plans_list)

@app.route('/dashboard')
@login_required
def customer_dashboard():
    if current_user.role == 'admin':
        return redirect(url_for('admin_dashboard'))
    
    # Carrega os dados do cliente
    customer = get_customer_by_id(current_user.id)
    
    # Carrega os veículos do cliente
    try:
        vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
        vehicles = vehicles_df[vehicles_df['customer_id'] == current_user.id].to_dict('records')
    except FileNotFoundError:
        vehicles = []
    
    # Carrega as assinaturas ativas do cliente
    try:
        subs_df = pd.read_csv(os.path.join(DATA_DIR, 'subscriptions.csv'))
        subs_df = subs_df[subs_df['customer_id'] == current_user.id]
        
        # Adiciona informações adicionais às assinaturas
        subscriptions = []
        for _, sub in subs_df.iterrows():
            sub_dict = sub.to_dict()
            vehicle = get_vehicle_by_id(sub['vehicle_id'])
            plan = get_plan_by_id(sub['plan_id'])
            
            sub_dict['vehicle_plate'] = vehicle['plate'] if vehicle else 'N/A'
            sub_dict['plan_name'] = plan['name'] if plan else 'N/A'
            subscriptions.append(sub_dict)
            
    except FileNotFoundError:
        subscriptions = []
    
    return render_template('customer/dashboard.html',
                         customer=customer,
                         vehicles=vehicles,
                         subscriptions=subscriptions)

# --- Rotas de Autenticação ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = LoginForm()
    if form.validate_on_submit():
        try:
            users_df = read_csv_cached('users.csv')
            if users_df.empty:
                flash('Erro no sistema: arquivo de usuários não encontrado.', 'danger')
                return render_template('auth/login.html', form=form)
            
            # Usar loc para busca mais eficiente
            user_data = users_df.loc[users_df['username'] == form.username.data]
            
            if not user_data.empty:
                user_record = user_data.iloc[0]
                if check_password_hash(user_record['password_hash'], form.password.data):
                    user_obj = User(
                        id=int(user_record['id']), 
                        username=user_record['username'], 
                        role=user_record['role'],
                        name=user_record.get('name', '')
                    )
                    login_user(user_obj)
                    
                    next_page = request.args.get('next')
                    return redirect(next_page or url_for('index'))
                else:
                    flash('Senha incorreta. Tente novamente.', 'danger')
            else:
                flash('Usuário não encontrado.', 'danger')
        except Exception as e:
            flash(f'Erro ao fazer login: {str(e)}', 'danger')

    return render_template('auth/login.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- Rotas de Perfil ---
# Rota de perfil desabilitada (ProfileForm não definido)
# @app.route('/profile', methods=['GET', 'POST'])
# @login_required
# def profile():
#     pass

# --- Rotas de Gerenciamento (Admin) ---

# --- Clientes ---
@app.route('/admin/customers')
@admin_required
def list_customers():
    try:
        customers_df = read_csv_cached('customers.csv')
        if customers_df.empty:
            return render_template('admin/customers/list.html', 
                                 customers=[], page=1, total_pages=0, total=0)
        
        # Buscar veículos uma única vez
        vehicles_df = read_csv_cached('vehicles.csv')
        
        # Aplicar filtros de busca
        search = request.args.get('search', '').strip()
        status = request.args.get('status', '')
        
        if search:
            search_normalized = ''.join(filter(str.isalnum, search.lower()))
            
            # Usar operações vetorizadas (muito mais rápidas)
            name_mask = customers_df['name'].str.lower().str.contains(search.lower(), na=False, regex=False)
            cpf_mask = customers_df['cpf'].fillna('').astype(str).str.replace(r'\D', '', regex=True).str.lower().str.contains(search_normalized, na=False, regex=False)
            
            # Busca por placa de veículo
            plate_matches = []
            if not vehicles_df.empty:
                plate_mask = vehicles_df['plate'].fillna('').astype(str).str.replace(r'\W', '', regex=True).str.lower().str.contains(search_normalized, na=False, regex=False)
                plate_matches = vehicles_df.loc[plate_mask, 'customer_id'].unique().tolist()
            
            # Combinar filtros
            if plate_matches:
                customers_df = customers_df[name_mask | cpf_mask | customers_df['id'].isin(plate_matches)]
            else:
                customers_df = customers_df[name_mask | cpf_mask]
        
        # Filtrar por status
        if status:
            customers_df = customers_df[customers_df['status'] == status]
        
        # Paginação
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(customers_df)
        total_pages = (total + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        customers_paginated = customers_df.iloc[start_idx:end_idx]
        customers = customers_paginated.to_dict('records')
        
        # Adicionar veículos usando merge (mais eficiente que loop)
        if not vehicles_df.empty and customers:
            customer_ids = [c['id'] for c in customers]
            customer_vehicles = vehicles_df[vehicles_df['customer_id'].isin(customer_ids)]
            
            # Agrupar veículos por cliente
            vehicles_by_customer = {}
            for _, v in customer_vehicles.iterrows():
                cid = v['customer_id']
                if cid not in vehicles_by_customer:
                    vehicles_by_customer[cid] = []
                vehicles_by_customer[cid].append(v.to_dict())
            
            # Adicionar veículos aos clientes
            for customer in customers:
                customer['vehicles'] = vehicles_by_customer.get(customer['id'], [])
        else:
            for customer in customers:
                customer['vehicles'] = []
        
        return render_template('admin/customers/list.html', 
                             customers=customers,
                             page=page,
                             total_pages=total_pages,
                             total=total)
    except Exception as e:
        print(f"Erro ao listar clientes: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Erro ao carregar clientes: {str(e)}', 'danger')
        return render_template('admin/customers/list.html', 
                             customers=[], page=1, total_pages=0, total=0)

@app.route('/admin/customers/add', methods=['GET', 'POST'])
@admin_required
def add_customer():
    if request.method == 'POST':
        try:
            customers_df = read_csv_cached('customers.csv')
            
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip()
            phone = request.form.get('phone', '').strip()
            cpf = request.form.get('cpf', '').strip()
            
            # Validações
            if not name or not email or not phone or not cpf:
                flash('Preencha todos os campos obrigatórios.', 'danger')
                return redirect(url_for('list_customers'))
            
            # Verifica se já existe um cliente com o mesmo CPF
            if not customers_df[customers_df['cpf'] == cpf].empty:
                flash('Já existe um cliente cadastrado com este CPF.', 'danger')
                return redirect(url_for('list_customers'))
            
            # Adiciona o novo cliente
            new_id = get_next_id('customers.csv')
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            new_customer = pd.DataFrame([{
                'id': new_id,
                'name': name,
                'email': email,
                'phone': phone,
                'phone2': request.form.get('phone2', '').strip(),
                'cpf': cpf,
                'rg': request.form.get('rg', '').strip(),
                'birth_date': request.form.get('birth_date', '').strip(),
                'cep': request.form.get('cep', '').strip(),
                'street': request.form.get('street', '').strip(),
                'number': request.form.get('number', '').strip(),
                'complement': request.form.get('complement', '').strip(),
                'neighborhood': request.form.get('neighborhood', '').strip(),
                'city': request.form.get('city', '').strip(),
                'state': request.form.get('state', '').strip(),
                'address': '',
                'notes': request.form.get('notes', '').strip(),
                'status': request.form.get('status', 'ativo'),
                'created_at': now,
                'updated_at': now
            }])
            
            updated_df = pd.concat([customers_df, new_customer], ignore_index=True)
            save_csv_and_invalidate(updated_df, 'customers.csv')
            
            flash('Cliente cadastrado com sucesso!', 'success')
            
            if request.form.get('from_dashboard') == '1':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('list_customers'))
            
        except Exception as e:
            flash(f'Erro ao cadastrar cliente: {str(e)}', 'danger')
            if request.form.get('from_dashboard') == '1':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('list_customers'))
    
    form = CustomerForm()
    return render_template('admin/customers/form.html', form=form, title='Adicionar Cliente')

@app.route('/admin/customers/edit/<int:customer_id>', methods=['GET', 'POST'])
@admin_required
def edit_customer(customer_id):
    try:
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        customer = customers_df[customers_df['id'] == customer_id].iloc[0].to_dict()
        
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip()
            phone = request.form.get('phone', '').strip()
            cpf = request.form.get('cpf', '').strip()
            
            # Validações
            if not name or not email or not phone or not cpf:
                flash('Preencha todos os campos obrigatórios.', 'danger')
                return redirect(url_for('list_customers'))
            
            # Verifica se o CPF já está em uso por outro cliente
            if not customers_df[(customers_df['cpf'] == cpf) & (customers_df['id'] != customer_id)].empty:
                flash('Já existe outro cliente cadastrado com este CPF.', 'danger')
                return redirect(url_for('list_customers'))
            
            # Atualiza o cliente
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            customers_df.loc[customers_df['id'] == customer_id, 'name'] = name
            customers_df.loc[customers_df['id'] == customer_id, 'email'] = email
            customers_df.loc[customers_df['id'] == customer_id, 'phone'] = phone
            customers_df.loc[customers_df['id'] == customer_id, 'phone2'] = request.form.get('phone2', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'cpf'] = cpf
            customers_df.loc[customers_df['id'] == customer_id, 'rg'] = request.form.get('rg', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'birth_date'] = request.form.get('birth_date', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'cep'] = request.form.get('cep', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'street'] = request.form.get('street', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'number'] = request.form.get('number', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'complement'] = request.form.get('complement', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'neighborhood'] = request.form.get('neighborhood', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'city'] = request.form.get('city', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'state'] = request.form.get('state', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'notes'] = request.form.get('notes', '').strip()
            customers_df.loc[customers_df['id'] == customer_id, 'status'] = request.form.get('status', 'ativo')
            customers_df.loc[customers_df['id'] == customer_id, 'updated_at'] = now
            
            customers_df.to_csv(os.path.join(DATA_DIR, 'customers.csv'), index=False)
            
            flash('Cliente atualizado com sucesso!', 'success')
            return redirect(url_for('list_customers'))
        
        # GET - renderizar formulário antigo para compatibilidade
        form = CustomerForm()
        form.name.data = customer['name']
        form.email.data = customer['email']
        form.phone.data = customer['phone']
        form.cpf.data = customer['cpf']
        form.address.data = customer.get('address', '')
        
        return render_template('admin/customers/form.html', form=form, title='Editar Cliente')
        
    except IndexError:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('list_customers'))
    except FileNotFoundError:
        flash('Erro: Arquivo de clientes não encontrado.', 'danger')
        return redirect(url_for('list_customers'))
    except Exception as e:
        flash(f'Erro ao editar cliente: {str(e)}', 'danger')
        return redirect(url_for('list_customers'))

@app.route('/admin/customers/delete/<int:customer_id>', methods=['POST'])
@admin_required
def delete_customer(customer_id):
    try:
        customers_df = read_csv_cached('customers.csv')
        if customers_df.empty or customer_id not in customers_df['id'].values:
            flash('Cliente não encontrado.', 'danger')
            return redirect(url_for('list_customers'))
        
        # Verifica se o cliente possui veículos cadastrados
        vehicles_df = read_csv_cached('vehicles.csv')
        if not vehicles_df.empty and not vehicles_df[vehicles_df['customer_id'] == customer_id].empty:
            flash('Não é possível excluir o cliente pois existem veículos vinculados a ele.', 'danger')
            return redirect(url_for('list_customers'))
        
        # Remove o cliente
        customers_df = customers_df[customers_df['id'] != customer_id]
        save_csv_and_invalidate(customers_df, 'customers.csv')
        
        flash('Cliente excluído com sucesso!', 'success')
        
    except Exception as e:
        flash(f'Erro ao excluir cliente: {str(e)}', 'danger')
    
    return redirect(url_for('list_customers'))

# --- Veículos ---
@app.route('/admin/vehicles')
@admin_required
def list_vehicles():
    try:
        vehicles_df = read_csv_cached('vehicles.csv')
        customers_df = read_csv_cached('customers.csv')
        
        if vehicles_df.empty:
            return render_template('admin/vehicles/list.html', 
                                 vehicles=[], customers=[], page=1, total_pages=0, total=0)
        
        # Merge eficiente com clientes
        if not customers_df.empty:
            vehicles_df = vehicles_df.merge(
                customers_df[['id', 'name']], 
                left_on='customer_id', 
                right_on='id', 
                how='left', 
                suffixes=('', '_customer')
            )
            vehicles_df.rename(columns={'name': 'customer_name'}, inplace=True)
        else:
            vehicles_df['customer_name'] = 'N/A'
        
        # Aplicar filtros
        search = request.args.get('search', '').strip()
        status = request.args.get('status', '')
        customer_filter = request.args.get('customer', '')
        
        if search:
            search_normalized = ''.join(filter(str.isalnum, search.lower()))
            # Usar operações vetorizadas
            mask = (
                vehicles_df['plate'].fillna('').astype(str).str.replace(r'\W', '', regex=True).str.lower().str.contains(search_normalized, na=False, regex=False) |
                vehicles_df['model'].fillna('').astype(str).str.lower().str.contains(search.lower(), na=False, regex=False) |
                vehicles_df['brand'].fillna('').astype(str).str.lower().str.contains(search.lower(), na=False, regex=False) |
                vehicles_df['customer_name'].fillna('').astype(str).str.lower().str.contains(search.lower(), na=False, regex=False)
            )
            vehicles_df = vehicles_df[mask]
        
        if status:
            vehicles_df = vehicles_df[vehicles_df['status'] == status]
        
        if customer_filter:
            vehicles_df = vehicles_df[vehicles_df['customer_id'] == int(customer_filter)]
        
        # Paginação
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(vehicles_df)
        total_pages = (total + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        vehicles_paginated = vehicles_df.iloc[start_idx:end_idx]
        
        customers_list = customers_df.to_dict('records') if not customers_df.empty else []
        
        return render_template('admin/vehicles/list.html', 
                             vehicles=vehicles_paginated.to_dict('records'),
                             customers=customers_list,
                             page=page,
                             total_pages=total_pages,
                             total=total)
    except Exception as e:
        print(f"Erro ao listar veículos: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Erro ao carregar veículos: {str(e)}', 'danger')
        return render_template('admin/vehicles/list.html', 
                             vehicles=[], customers=[], page=1, total_pages=0, total=0)

@app.route('/admin/vehicles/add', methods=['GET', 'POST'])
@admin_required
def add_vehicle():
    if request.method == 'POST':
        try:
            vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
            
            plate = request.form.get('plate', '').strip().upper()
            customer_id = request.form.get('customer_id', '')
            brand = request.form.get('brand', '').strip()
            model = request.form.get('model', '').strip()
            
            # Validações
            if not plate or not customer_id or not brand or not model:
                flash('Preencha todos os campos obrigatórios.', 'danger')
                return redirect(url_for('list_vehicles'))
            
            # Verifica se já existe um veículo com a mesma placa
            if not vehicles_df[vehicles_df['plate'] == plate].empty:
                flash('Já existe um veículo cadastrado com esta placa.', 'danger')
                return redirect(url_for('list_vehicles'))
            
            # Adiciona o novo veículo
            new_id = get_next_id('vehicles.csv')
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            new_vehicle = pd.DataFrame([{
                'id': new_id,
                'customer_id': int(customer_id),
                'plate': plate,
                'brand': brand,
                'model': model,
                'color': request.form.get('color_name', '').strip(),
                'year': request.form.get('year', '').strip(),
                'type': request.form.get('type', '').strip(),
                'renavam': request.form.get('renavam', '').strip(),
                'chassis': request.form.get('chassis', '').strip().upper(),
                'notes': request.form.get('notes', '').strip(),
                'status': request.form.get('status', 'ativo'),
                'created_at': now,
                'updated_at': now
            }])
            
            updated_df = pd.concat([vehicles_df, new_vehicle], ignore_index=True)
            updated_df.to_csv(os.path.join(DATA_DIR, 'vehicles.csv'), index=False)
            
            flash('Veículo cadastrado com sucesso!', 'success')
            
            # Verifica se veio do dashboard
            if request.form.get('from_dashboard') == '1':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('list_vehicles'))
            
        except Exception as e:
            flash(f'Erro ao cadastrar veículo: {str(e)}', 'danger')
            if request.form.get('from_dashboard') == '1':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('list_vehicles'))
    
    # GET - renderizar formulário antigo para compatibilidade
    form = VehicleForm()
    try:
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        form.customer_id.choices = [(row['id'], row['name']) for _, row in customers_df.iterrows()]
    except FileNotFoundError:
        flash('Nenhum cliente cadastrado. Cadastre um cliente antes de adicionar um veículo.', 'warning')
        return redirect(url_for('add_customer'))
    
    return render_template('admin/vehicles/form.html', form=form, title='Adicionar Veículo')

@app.route('/admin/vehicles/edit/<int:vehicle_id>', methods=['GET', 'POST'])
@admin_required
def edit_vehicle(vehicle_id):
    try:
        vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
        vehicle = vehicles_df[vehicles_df['id'] == vehicle_id].iloc[0].to_dict()
        
        if request.method == 'POST':
            plate = request.form.get('plate', '').strip().upper()
            customer_id = request.form.get('customer_id', '')
            brand = request.form.get('brand', '').strip()
            model = request.form.get('model', '').strip()
            
            # Validações
            if not plate or not customer_id or not brand or not model:
                flash('Preencha todos os campos obrigatórios.', 'danger')
                return redirect(url_for('list_vehicles'))
            
            # Verifica se a placa já está em uso por outro veículo
            if not vehicles_df[(vehicles_df['plate'] == plate) & (vehicles_df['id'] != vehicle_id)].empty:
                flash('Já existe outro veículo cadastrado com esta placa.', 'danger')
                return redirect(url_for('list_vehicles'))
            
            # Atualiza o veículo
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'customer_id'] = int(customer_id)
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'plate'] = plate
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'brand'] = brand
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'model'] = model
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'color'] = request.form.get('color_name', '').strip()
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'year'] = request.form.get('year', '').strip()
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'type'] = request.form.get('type', '').strip()
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'renavam'] = request.form.get('renavam', '').strip()
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'chassis'] = request.form.get('chassis', '').strip().upper()
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'notes'] = request.form.get('notes', '').strip()
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'status'] = request.form.get('status', 'ativo')
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'updated_at'] = now
            
            vehicles_df.to_csv(os.path.join(DATA_DIR, 'vehicles.csv'), index=False)
            
            flash('Veículo atualizado com sucesso!', 'success')
            return redirect(url_for('list_vehicles'))
        
        # GET - renderizar formulário antigo para compatibilidade
        form = VehicleForm()
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        form.customer_id.choices = [(row['id'], row['name']) for _, row in customers_df.iterrows()]
        
        form.id.data = vehicle['id']
        form.customer_id.data = int(vehicle['customer_id'])
        form.plate.data = vehicle['plate']
        form.brand.data = vehicle.get('brand', '')
        form.model.data = vehicle['model']
        form.color_name.data = vehicle.get('color', '')
        form.year.data = int(vehicle['year']) if pd.notna(vehicle.get('year')) and vehicle.get('year') != '' else None
        form.type.data = vehicle.get('type', '')
        form.renavam.data = vehicle.get('renavam', '')
        form.chassis.data = vehicle.get('chassis', '')
        form.notes.data = vehicle.get('notes', '')
        form.status.data = vehicle.get('status', 'ativo')
        
        if form.validate_on_submit():
            # Verifica se a placa já está em uso por outro veículo
            if not vehicles_df[(vehicles_df['plate'] == form.plate.data.upper()) & 
                              (vehicles_df['id'] != vehicle_id)].empty:
                flash('Já existe um veículo cadastrado com esta placa.', 'danger')
                return render_template('admin/vehicles/form.html', form=form, title='Editar Veículo')
            
            # Atualiza os dados do veículo
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'customer_id'] = form.customer_id.data
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'plate'] = form.plate.data.upper()
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'brand'] = form.brand.data if form.brand.data else ''
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'model'] = form.model.data
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'color'] = form.color_name.data if form.color_name.data else ''
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'year'] = form.year.data if form.year.data else ''
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'type'] = form.type.data if form.type.data else ''
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'renavam'] = form.renavam.data if form.renavam.data else ''
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'chassis'] = form.chassis.data.upper() if form.chassis.data else ''
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'notes'] = form.notes.data if form.notes.data else ''
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'status'] = form.status.data if form.status.data else 'ativo'
            vehicles_df.loc[vehicles_df['id'] == vehicle_id, 'updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            vehicles_df.to_csv(os.path.join(DATA_DIR, 'vehicles.csv'), index=False)
            flash('Veículo atualizado com sucesso!', 'success')
            return redirect(url_for('list_vehicles'))
        
        return render_template('admin/vehicles/form.html', form=form, title='Editar Veículo')
        
    except IndexError:
        flash('Veículo não encontrado.', 'danger')
        return redirect(url_for('list_vehicles'))
    except FileNotFoundError:
        flash('Erro: Arquivo de veículos não encontrado.', 'danger')
        return redirect(url_for('list_vehicles'))
    except Exception as e:
        flash(f'Erro ao editar veículo: {str(e)}', 'danger')
        return redirect(url_for('list_vehicles'))

@app.route('/admin/vehicles/delete/<int:vehicle_id>', methods=['POST'])
@admin_required
def delete_vehicle(vehicle_id):
    try:
        vehicles_df = read_csv_cached('vehicles.csv')
        if vehicles_df.empty or vehicle_id not in vehicles_df['id'].values:
            flash('Veículo não encontrado.', 'danger')
            return redirect(url_for('list_vehicles'))
        
        # Verifica se o veículo está vinculado a alguma assinatura ativa
        subs_df = read_csv_cached('subscriptions.csv')
        if not subs_df.empty:
            subs_df['end_date'] = pd.to_datetime(subs_df['end_date'], errors='coerce')
            active_subs = subs_df[(subs_df['vehicle_id'] == vehicle_id) & 
                                 (subs_df['end_date'] >= datetime.now())]
            
            if not active_subs.empty:
                flash('Não é possível excluir o veículo pois ele está vinculado a uma assinatura ativa.', 'danger')
                return redirect(url_for('list_vehicles'))
        
        # Remove o veículo
        vehicles_df = vehicles_df[vehicles_df['id'] != vehicle_id]
        save_csv_and_invalidate(vehicles_df, 'vehicles.csv')
        
        flash('Veículo excluído com sucesso!', 'success')
        
    except Exception as e:
        flash(f'Erro ao excluir veículo: {str(e)}', 'danger')
    
    return redirect(url_for('list_vehicles'))

# --- Visualização Detalhada do Veículo ---
@app.route('/admin/vehicles/view/<int:vehicle_id>')
@admin_required
def view_vehicle(vehicle_id):
    try:
        # Carrega os dados do veículo
        vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
        vehicles_df['id'] = vehicles_df['id'].astype(int)  # Garante que o ID seja inteiro
        
        # Filtra o veículo pelo ID
        vehicle = vehicles_df[vehicles_df['id'] == vehicle_id].iloc[0].to_dict()
        
        # Converte datas do veículo de string para datetime
        if 'created_at' in vehicle and pd.notna(vehicle['created_at']):
            vehicle['created_at'] = pd.to_datetime(vehicle['created_at'])
        if 'updated_at' in vehicle and pd.notna(vehicle['updated_at']):
            vehicle['updated_at'] = pd.to_datetime(vehicle['updated_at'])
        
        # Carrega os dados do cliente proprietário
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        customer = customers_df[customers_df['id'] == vehicle['customer_id']].iloc[0].to_dict()
        vehicle['customer'] = customer
        
        # Carrega as fotos do veículo (se houver)
        try:
            photos_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicle_photos.csv'))
            vehicle_photos = photos_df[photos_df['vehicle_id'] == vehicle_id].to_dict('records')
            # Adiciona o caminho completo para as fotos e converte datas
            for photo in vehicle_photos:
                photo['url'] = f"uploads/vehicles/{photo['filename']}"
                # Converte created_at e upload_date de string para datetime
                if 'created_at' in photo and pd.notna(photo['created_at']):
                    photo['created_at'] = pd.to_datetime(photo['created_at'])
                if 'upload_date' in photo and pd.notna(photo['upload_date']):
                    photo['upload_date'] = pd.to_datetime(photo['upload_date'])
            vehicle['photos'] = vehicle_photos
        except FileNotFoundError:
            vehicle['photos'] = []
        
        # Carrega as movimentações do veículo (entradas/saídas)
        movements = []
        try:
            movements_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicle_movements.csv'))
            movements_df = movements_df[movements_df['vehicle_id'] == vehicle_id]
            
            if not movements_df.empty:
                # Ordena por data mais recente primeiro
                movements_df['date_time'] = pd.to_datetime(movements_df['date_time'], errors='coerce')
                movements_df = movements_df.sort_values('date_time', ascending=False)
                
                # Adiciona o nome do usuário que registrou a movimentação
                users_df = pd.read_csv(os.path.join(DATA_DIR, 'users.csv'))
                movements_df = pd.merge(movements_df, users_df[['id', 'name']], 
                                     left_on='user_id', right_on='id', 
                                     how='left', suffixes=('', '_user'))
                
                movements = movements_df.to_dict('records')
                # Adiciona o objeto user em cada movimentação
                for mov in movements:
                    mov['user'] = {'name': mov.get('name', 'Desconhecido')}
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Erro ao carregar movimentações: {e}")
        
        # Carrega os serviços realizados no veículo
        services = []
        try:
            services_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicle_services.csv'))
            services_df = services_df[services_df['vehicle_id'] == vehicle_id]
            
            if not services_df.empty:
                # Ordena por data mais recente primeiro
                services_df['date'] = pd.to_datetime(services_df['date'], errors='coerce')
                services_df = services_df.sort_values('date', ascending=False)
                services = services_df.to_dict('records')
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Erro ao carregar serviços: {e}")
        
        # Carrega os documentos do veículo
        documents = []
        try:
            docs_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicle_documents.csv'))
            docs_df = docs_df[docs_df['vehicle_id'] == vehicle_id]
            
            if not docs_df.empty:
                docs_df['expiration_date'] = pd.to_datetime(docs_df['expiration_date'], errors='coerce')
                documents = docs_df.to_dict('records')
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Erro ao carregar documentos: {e}")
        
        # Carrega o histórico de alterações do veículo
        history = []
        try:
            history_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicle_history.csv'))
            history_df = history_df[history_df['vehicle_id'] == vehicle_id]
            
            if not history_df.empty:
                # Ordena por data mais recente primeiro
                history_df['created_at'] = pd.to_datetime(history_df['created_at'], errors='coerce')
                if 'date' in history_df.columns:
                    history_df['date'] = pd.to_datetime(history_df['date'], errors='coerce')
                history_df = history_df.sort_values('created_at', ascending=False)
                
                # Adiciona o nome do usuário que fez a alteração
                users_df = pd.read_csv(os.path.join(DATA_DIR, 'users.csv'))
                history_df = pd.merge(history_df, users_df[['id', 'name']], 
                                    left_on='user_id', right_on='id', 
                                    how='left', suffixes=('', '_user'))
                
                # Converte as alterações de string JSON para dicionário
                import json
                history = history_df.to_dict('records')
                for item in history:
                    item['user'] = {'name': item.get('name', 'Desconhecido')}
                    try:
                        item['changes'] = json.loads(item.get('changes', '{}'))
                    except:
                        item['changes'] = {}
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Erro ao carregar histórico: {e}")
        
        return render_template('admin/vehicles/view.html', 
                             vehicle=vehicle, 
                             movements=movements,
                             services=services,
                             documents=documents,
                             history=history,
                             today=pd.Timestamp.now())
        
    except IndexError:
        flash('Veículo não encontrado.', 'danger')
        return redirect(url_for('list_vehicles'))
    except Exception as e:
        flash(f'Erro ao carregar os dados do veículo: {str(e)}', 'danger')
        return redirect(url_for('list_vehicles'))

# --- Planos ---
@app.route('/admin/plans')
@admin_required
def list_plans():
    try:
        plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
        
        # Aplicar filtros
        search = request.args.get('search', '').strip()
        status = request.args.get('status', '')
        
        if search:
            # Buscar por nome ou descrição
            mask = (
                plans_df['name'].fillna('').astype(str).str.lower().str.contains(search.lower(), na=False) |
                plans_df['description'].fillna('').astype(str).str.lower().str.contains(search.lower(), na=False)
            )
            plans_df = plans_df[mask]
        
        if status:
            is_active = status == 'ativo'
            plans_df = plans_df[plans_df['is_active'] == is_active]
        
        # Paginação
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(plans_df)
        total_pages = (total + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        plans_paginated = plans_df.iloc[start_idx:end_idx]
        
        return render_template('admin/plans/list.html', 
                             plans=plans_paginated.to_dict('records'),
                             page=page,
                             total_pages=total_pages,
                             total=total)
    except FileNotFoundError:
        return render_template('admin/plans/list.html', 
                             plans=[],
                             page=1,
                             total_pages=0,
                             total=0)
    except Exception as e:
        print(f"Erro ao listar planos: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Erro ao carregar planos: {str(e)}', 'danger')
        return render_template('admin/plans/list.html', 
                             plans=[],
                             page=1,
                             total_pages=0,
                             total=0)

@app.route('/admin/plans/add', methods=['GET', 'POST'])
@admin_required
def add_plan():
    if request.method == 'POST':
        try:
            plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
            
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price', '0')
            duration_days = request.form.get('duration_days', '0')
            
            # Validações
            if not name:
                flash('Nome do plano é obrigatório.', 'danger')
                return redirect(url_for('list_plans'))
            
            # Verifica se já existe um plano com o mesmo nome
            if not plans_df[plans_df['name'].str.lower() == name.lower()].empty:
                flash('Já existe um plano com este nome.', 'danger')
                return redirect(url_for('list_plans'))
            
            # Adiciona o novo plano
            new_id = get_next_id('plans.csv')
            new_plan = pd.DataFrame([{
                'id': new_id,
                'name': name,
                'description': description,
                'price': float(price),
                'duration_days': int(duration_days),
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }])
            
            updated_df = pd.concat([plans_df, new_plan], ignore_index=True)
            updated_df.to_csv(os.path.join(DATA_DIR, 'plans.csv'), index=False)
            
            flash('Plano cadastrado com sucesso!', 'success')
            return redirect(url_for('list_plans'))
            
        except Exception as e:
            flash(f'Erro ao cadastrar plano: {str(e)}', 'danger')
            return redirect(url_for('list_plans'))
    
    # GET - renderizar formulário antigo para compatibilidade
    form = PlanForm()
    return render_template('admin/plans/form.html', form=form, title='Adicionar Plano')

@app.route('/admin/plans/edit/<int:plan_id>', methods=['GET', 'POST'])
@admin_required
def edit_plan(plan_id):
    try:
        plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
        plan = plans_df[plans_df['id'] == plan_id].iloc[0].to_dict()
        
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            description = request.form.get('description', '').strip()
            price = request.form.get('price', '0')
            duration_days = request.form.get('duration_days', '0')
            
            # Validações
            if not name:
                flash('Nome do plano é obrigatório.', 'danger')
                return redirect(url_for('list_plans'))
            
            # Verifica se o nome já está em uso por outro plano
            if not plans_df[(plans_df['name'].str.lower() == name.lower()) & 
                           (plans_df['id'] != plan_id)].empty:
                flash('Já existe um plano com este nome.', 'danger')
                return redirect(url_for('list_plans'))
            
            # Atualiza o plano
            plans_df.loc[plans_df['id'] == plan_id, 'name'] = name
            plans_df.loc[plans_df['id'] == plan_id, 'description'] = description
            plans_df.loc[plans_df['id'] == plan_id, 'price'] = float(price)
            plans_df.loc[plans_df['id'] == plan_id, 'duration_days'] = int(duration_days)
            
            plans_df.to_csv(os.path.join(DATA_DIR, 'plans.csv'), index=False)
            
            flash('Plano atualizado com sucesso!', 'success')
            return redirect(url_for('list_plans'))
        
        # GET - renderizar formulário antigo para compatibilidade
        form = PlanForm()
        form.name.data = plan['name']
        form.description.data = plan['description']
        form.price.data = plan['price']
        form.duration_days.data = plan['duration_days']
        
        return render_template('admin/plans/form.html', form=form, title='Editar Plano')
    
    except Exception as e:
        flash(f'Erro ao processar plano: {str(e)}', 'danger')
        return redirect(url_for('list_plans'))
        
        return render_template('admin/plans/form.html', form=form, title='Editar Plano')
        
    except IndexError:
        flash('Plano não encontrado.', 'danger')
        return redirect(url_for('list_plans'))
    except FileNotFoundError:
        flash('Erro: Arquivo de planos não encontrado.', 'danger')
        return redirect(url_for('list_plans'))
    except Exception as e:
        flash(f'Erro ao editar plano: {str(e)}', 'danger')
        return redirect(url_for('list_plans'))

@app.route('/admin/plans/toggle/<int:plan_id>', methods=['POST'])
@admin_required
def toggle_plan(plan_id):
    try:
        plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
        
        # Alterna o status do plano
        current_status = plans_df.loc[plans_df['id'] == plan_id, 'is_active'].values[0]
        plans_df.loc[plans_df['id'] == plan_id, 'is_active'] = not current_status
        plans_df.to_csv(os.path.join(DATA_DIR, 'plans.csv'), index=False)
        
        status = 'ativado' if not current_status else 'desativado'
        flash(f'Plano {status} com sucesso!', 'success')
        
    except Exception as e:
        flash(f'Erro ao alterar status do plano: {str(e)}', 'danger')
    
    return redirect(url_for('list_plans'))

# --- Assinaturas ---
@app.route('/admin/subscriptions')
@admin_required
def list_subscriptions():
    try:
        subs_df = pd.read_csv(os.path.join(DATA_DIR, 'subscriptions.csv'))
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
        
        # Aplicar filtros
        search = request.args.get('search', '').strip()
        status = request.args.get('status', '')
        customer_filter = request.args.get('customer', '')
        
        # Adiciona informações adicionais às assinaturas
        subscriptions = []
        total_monthly = 0
        
        for _, sub in subs_df.iterrows():
            sub_dict = sub.to_dict()
            
            # Obtém informações do cliente
            customer = get_customer_by_id(sub['customer_id'])
            sub_dict['customer_name'] = customer['name'] if customer else 'Cliente não encontrado'
            
            # Obtém informações do veículo
            vehicle = get_vehicle_by_id(sub['vehicle_id'])
            sub_dict['vehicle_plate'] = vehicle['plate'] if vehicle else 'Veículo não encontrado'
            
            # Obtém informações do plano
            plan = get_plan_by_id(sub['plan_id'])
            sub_dict['plan_name'] = plan['name'] if plan else 'Plano não encontrado'
            
            # Mantém as datas originais e cria versões formatadas
            sub_dict['start_date_raw'] = sub['start_date']
            sub_dict['start_date'] = pd.to_datetime(sub['start_date']).strftime('%d/%m/%Y')
            sub_dict['end_date'] = pd.to_datetime(sub['end_date']).strftime('%d/%m/%Y')
            
            # Verifica se a assinatura está ativa
            sub_dict['is_active'] = pd.to_datetime(sub['end_date']) >= datetime.now()
            
            # Soma o valor se a assinatura estiver ativa
            if sub_dict['is_active']:
                total_monthly += float(sub.get('amount', 0))
            
            subscriptions.append(sub_dict)
        
        # Aplicar filtros
        if search:
            search_lower = search.lower()
            subscriptions = [s for s in subscriptions if 
                           search_lower in s['customer_name'].lower() or 
                           search_lower in s['vehicle_plate'].lower() or 
                           search_lower in s['plan_name'].lower()]
        
        if status:
            if status == 'ativa':
                subscriptions = [s for s in subscriptions if s['is_active']]
            elif status == 'inativa':
                subscriptions = [s for s in subscriptions if not s['is_active']]
        
        if customer_filter:
            subscriptions = [s for s in subscriptions if s['customer_id'] == int(customer_filter)]
        
        # Paginação
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(subscriptions)
        total_pages = (total + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        subscriptions_paginated = subscriptions[start_idx:end_idx]
        
        # Prepara listas para o modal
        customers = [{'id': int(row['id']), 'name': row['name']} for _, row in customers_df.iterrows()]
        plans = [{'id': int(row['id']), 'name': row['name'], 'price': float(row['price'])} 
                for _, row in plans_df[plans_df['is_active'] == True].iterrows()]
        
        return render_template('admin/subscriptions/list.html', 
                             subscriptions=subscriptions_paginated, 
                             total_monthly=total_monthly,
                             customers=customers,
                             plans=plans,
                             page=page,
                             total_pages=total_pages,
                             total=total)
        
    except FileNotFoundError:
        return render_template('admin/subscriptions/list.html', 
                             subscriptions=[], 
                             total_monthly=0,
                             customers=[],
                             plans=[],
                             page=1,
                             total_pages=0,
                             total=0)
    except Exception as e:
        print(f"Erro ao listar assinaturas: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Erro ao carregar assinaturas: {str(e)}', 'danger')
        return render_template('admin/subscriptions/list.html', 
                             subscriptions=[], 
                             total_monthly=0,
                             customers=[],
                             plans=[],
                             page=1,
                             total_pages=0,
                             total=0)

@app.route('/admin/subscriptions/add', methods=['GET', 'POST'])
@admin_required
def add_subscription():
    if request.method == 'POST':
        # Processa dados do modal
        try:
            subscription_id = request.form.get('subscription_id')
            customer_id = int(request.form.get('customer_id'))
            vehicle_id = int(request.form.get('vehicle_id'))
            plan_id = int(request.form.get('plan_id'))
            start_date_str = request.form.get('start_date')
            
            print(f"DEBUG - subscription_id recebido: '{subscription_id}' (tipo: {type(subscription_id)})")
            print(f"DEBUG - Dados recebidos: customer={customer_id}, vehicle={vehicle_id}, plan={plan_id}, date={start_date_str}")
            
            # Validações
            if not all([customer_id, vehicle_id, plan_id, start_date_str]):
                flash('Todos os campos são obrigatórios.', 'danger')
                return redirect(url_for('list_subscriptions'))
            
            # Carrega os dados necessários
            vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
            plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
            subs_df = pd.read_csv(os.path.join(DATA_DIR, 'subscriptions.csv'))
            
            # Verifica se o veículo pertence ao cliente
            vehicle = vehicles_df[vehicles_df['id'] == vehicle_id]
            if vehicle.empty or int(vehicle.iloc[0]['customer_id']) != customer_id:
                flash('O veículo selecionado não pertence ao cliente escolhido.', 'danger')
                return redirect(url_for('list_subscriptions'))
            
            # Obtém os dados do plano selecionado
            plan = plans_df[plans_df['id'] == plan_id].iloc[0]
            
            # Calcula a data de término com base na duração do plano
            start_date = pd.to_datetime(start_date_str).date()
            end_date = start_date + timedelta(days=int(plan['duration_days']))
            
            # Se tem subscription_id, é uma edição
            if subscription_id and subscription_id != '':
                subscription_id = int(subscription_id)
                subs_df.loc[subs_df['id'] == subscription_id, 'customer_id'] = customer_id
                subs_df.loc[subs_df['id'] == subscription_id, 'vehicle_id'] = vehicle_id
                subs_df.loc[subs_df['id'] == subscription_id, 'plan_id'] = plan_id
                subs_df.loc[subs_df['id'] == subscription_id, 'amount'] = float(plan['price'])
                subs_df.loc[subs_df['id'] == subscription_id, 'start_date'] = start_date.strftime('%Y-%m-%d')
                subs_df.loc[subs_df['id'] == subscription_id, 'end_date'] = end_date.strftime('%Y-%m-%d')
                
                subs_df.to_csv(os.path.join(DATA_DIR, 'subscriptions.csv'), index=False)
                flash('Assinatura atualizada com sucesso!', 'success')
            else:
                # Cria a nova assinatura
                new_id = get_next_id('subscriptions.csv')
                new_sub = pd.DataFrame([{
                    'id': new_id,
                    'customer_id': customer_id,
                    'vehicle_id': vehicle_id,
                    'plan_id': plan_id,
                    'amount': float(plan['price']),
                    'start_date': start_date.strftime('%Y-%m-%d'),
                    'end_date': end_date.strftime('%Y-%m-%d'),
                    'status': 'ativa',
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }])
                
                updated_df = pd.concat([subs_df, new_sub], ignore_index=True)
                updated_df.to_csv(os.path.join(DATA_DIR, 'subscriptions.csv'), index=False)
                flash('Assinatura cadastrada com sucesso!', 'success')
            
            # Verifica se veio do dashboard
            if request.form.get('from_dashboard') == '1':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('list_subscriptions'))
            
        except Exception as e:
            flash(f'Erro ao salvar assinatura: {str(e)}', 'danger')
            if request.form.get('from_dashboard') == '1':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('list_subscriptions'))
    
    # GET - Formulário tradicional (se ainda for usado)
    form = SubscriptionForm()
    
    try:
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        form.customer_id.choices = [(row['id'], row['name']) for _, row in customers_df.iterrows()]
        
        vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
        form.vehicle_id.choices = [(row['id'], f"{row['plate']} - {row['model']}") for _, row in vehicles_df.iterrows()]
        
        plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
        form.plan_id.choices = [(row['id'], f"{row['name']} (R$ {row['price']:.2f} - {row['duration_days']} dias)") 
                              for _, row in plans_df[plans_df['is_active'] == True].iterrows()]
    except FileNotFoundError as e:
        flash('Erro ao carregar dados necessários.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('admin/subscriptions/form.html', form=form, title='Nova Assinatura')

@app.route('/admin/subscriptions/edit/<int:subscription_id>', methods=['GET', 'POST'])
@admin_required
def edit_subscription(subscription_id):
    form = SubscriptionForm()
    
    # Preenche as opções de clientes, veículos e planos
    try:
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        form.customer_id.choices = [(row['id'], row['name']) for _, row in customers_df.iterrows()]
        
        vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
        form.vehicle_id.choices = [(row['id'], f"{row['plate']} - {row['model']}") for _, row in vehicles_df.iterrows()]
        
        plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
        form.plan_id.choices = [(row['id'], f"{row['name']} (R$ {row['price']:.2f} - {row['duration_days']} dias)") 
                              for _, row in plans_df[plans_df['is_active'] == True].iterrows()]
    except FileNotFoundError as e:
        flash('Erro ao carregar dados necessários.', 'danger')
        return redirect(url_for('list_subscriptions'))
    
    try:
        subs_df = pd.read_csv(os.path.join(DATA_DIR, 'subscriptions.csv'))
        subscription = subs_df[subs_df['id'] == subscription_id].iloc[0]
        
        if request.method == 'GET':
            # Preenche o formulário com os dados existentes
            form.customer_id.data = int(subscription['customer_id'])
            form.vehicle_id.data = int(subscription['vehicle_id'])
            form.plan_id.data = int(subscription['plan_id'])
            form.start_date.data = pd.to_datetime(subscription['start_date']).date()
        
        if form.validate_on_submit():
            # Obtém os dados do plano selecionado
            plan = plans_df[plans_df['id'] == form.plan_id.data].iloc[0]
            
            # Calcula a data de término com base na duração do plano
            start_date = form.start_date.data
            end_date = start_date + timedelta(days=int(plan['duration_days']))
            
            # Atualiza a assinatura
            subs_df.loc[subs_df['id'] == subscription_id, 'customer_id'] = form.customer_id.data
            subs_df.loc[subs_df['id'] == subscription_id, 'vehicle_id'] = form.vehicle_id.data
            subs_df.loc[subs_df['id'] == subscription_id, 'plan_id'] = form.plan_id.data
            subs_df.loc[subs_df['id'] == subscription_id, 'amount'] = float(plan['price'])
            subs_df.loc[subs_df['id'] == subscription_id, 'start_date'] = start_date.strftime('%Y-%m-%d')
            subs_df.loc[subs_df['id'] == subscription_id, 'end_date'] = end_date.strftime('%Y-%m-%d')
            
            subs_df.to_csv(os.path.join(DATA_DIR, 'subscriptions.csv'), index=False)
            
            flash('Assinatura atualizada com sucesso!', 'success')
            return redirect(url_for('list_subscriptions'))
    
    except Exception as e:
        flash(f'Erro ao editar assinatura: {str(e)}', 'danger')
        return redirect(url_for('list_subscriptions'))
    
    return render_template('admin/subscriptions/form.html', form=form, title='Editar Assinatura')

@app.route('/admin/subscriptions/delete/<int:subscription_id>', methods=['POST'])
@admin_required
def delete_subscription(subscription_id):
    try:
        subs_df = read_csv_cached('subscriptions.csv')
        if subs_df.empty:
            flash('Assinatura não encontrada.', 'danger')
            return redirect(url_for('list_subscriptions'))
        
        subs_df = subs_df[subs_df['id'] != subscription_id]
        save_csv_and_invalidate(subs_df, 'subscriptions.csv')
        
        flash('Assinatura excluída com sucesso!', 'success')
    except Exception as e:
        flash(f'Erro ao excluir assinatura: {str(e)}', 'danger')
    
    return redirect(url_for('list_subscriptions'))

# --- Financeiro ---
@app.route('/admin/financial/transactions')
@admin_required
def financial_transactions():
    try:
        transactions_df = pd.read_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'))
        transactions_df['date'] = pd.to_datetime(transactions_df['date'])
        transactions_df = transactions_df.sort_values('date', ascending=False)
        
        # Filtros
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        transaction_type = request.args.get('type')
        
        if start_date:
            transactions_df = transactions_df[transactions_df['date'] >= pd.to_datetime(start_date)]
        if end_date:
            transactions_df = transactions_df[transactions_df['date'] <= pd.to_datetime(end_date) + timedelta(days=1)]
        if transaction_type:
            transactions_df = transactions_df[transactions_df['type'] == transaction_type]
        
        # Calcula totais
        total_receita = transactions_df[transactions_df['type'] == 'receita']['amount'].sum()
        total_despesa = transactions_df[transactions_df['type'] == 'despesa']['amount'].sum()
        saldo = total_receita - total_despesa
        
        # Formata os dados para exibição
        transactions = []
        for _, row in transactions_df.iterrows():
            trans = row.to_dict()
            trans['date'] = trans['date'].strftime('%d/%m/%Y')
            trans['amount'] = f"R$ {trans['amount']:,.2f}".replace('.', '|').replace(',', '.').replace('|', ',')
            transactions.append(trans)
        
        return render_template('admin/financial/transactions.html',
                             transactions=transactions,
                             total_receita=total_receita,
                             total_despesa=total_despesa,
                             saldo=saldo,
                             start_date=start_date,
                             end_date=end_date,
                             transaction_type=transaction_type)
        
    except FileNotFoundError:
        return render_template('admin/financial/transactions.html',
                             transactions=[],
                             total_receita=0,
                             total_despesa=0,
                             saldo=0)

@app.route('/admin/financial/transactions/add', methods=['GET', 'POST'])
@admin_required
def add_financial_transaction():
    form = FinancialTransactionForm()
    
    if form.validate_on_submit():
        try:
            transactions_df = pd.read_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'))
            
            new_id = get_next_id('financial_transactions.csv')
            new_transaction = pd.DataFrame([{
                'id': new_id,
                'description': form.description.data,
                'amount': float(form.amount.data),
                'date': form.transaction_date.data.strftime('%Y-%m-%d'),
                'category': form.category.data,
                'type': form.type.data,
                'related_id': None,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }])
            
            updated_df = pd.concat([transactions_df, new_transaction], ignore_index=True)
            updated_df.to_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'), index=False)
            
            flash('Transação registrada com sucesso!', 'success')
            return redirect(url_for('financial_transactions'))
            
        except Exception as e:
            flash(f'Erro ao registrar transação: {str(e)}', 'danger')
    
    return render_template('admin/financial/transaction_form.html', form=form, title='Nova Transação')

# --- Contas a Receber ---
@app.route('/admin/financial/accounts-receivable')
@admin_required
def accounts_receivable():
    try:
        # Carrega assinaturas
        subscriptions_df = pd.read_csv(os.path.join(DATA_DIR, 'subscriptions.csv'))
        customers_df = pd.read_csv(os.path.join(DATA_DIR, 'customers.csv'))
        plans_df = pd.read_csv(os.path.join(DATA_DIR, 'plans.csv'))
        
        # Filtros
        search = request.args.get('search', '').strip()
        status_filter = request.args.get('status', '')
        customer_filter = request.args.get('customer', '')
        
        # Carrega contas a receber existentes
        try:
            receivables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_receivable.csv'))
            if receivables_df.empty or 'subscription_id' not in receivables_df.columns:
                receivables_df = pd.DataFrame(columns=['id', 'subscription_id', 'customer_id', 'description', 
                                                       'amount', 'due_date', 'payment_date', 'status', 
                                                       'payment_method', 'notes', 'created_at', 'updated_at'])
        except (FileNotFoundError, pd.errors.EmptyDataError):
            receivables_df = pd.DataFrame(columns=['id', 'subscription_id', 'customer_id', 'description', 
                                                   'amount', 'due_date', 'payment_date', 'status', 
                                                   'payment_method', 'notes', 'created_at', 'updated_at'])
        
        receivables_list = []
        next_id = receivables_df['id'].max() + 1 if len(receivables_df) > 0 and not pd.isna(receivables_df['id'].max()) else 1
        
        # Processa cada assinatura
        for _, sub in subscriptions_df.iterrows():
            # Busca se já existe conta a receber para esta assinatura
            existing = receivables_df[receivables_df['subscription_id'] == sub['id']]
            
            # Obtém dados do cliente e plano
            customer = customers_df[customers_df['id'] == sub['customer_id']].iloc[0] if len(customers_df[customers_df['id'] == sub['customer_id']]) > 0 else None
            plan = plans_df[plans_df['id'] == sub['plan_id']].iloc[0] if len(plans_df[plans_df['id'] == sub['plan_id']]) > 0 else None
            
            customer_name = customer['name'] if customer is not None else 'Cliente não encontrado'
            plan_name = plan['name'] if plan is not None else 'Plano não encontrado'
            
            end_date = pd.to_datetime(sub['end_date'])
            today = pd.Timestamp.now()
            
            if len(existing) > 0:
                # Usa conta existente
                rec = existing.iloc[0].to_dict()
                rec['customer_name'] = customer_name
                rec['due_date_formatted'] = pd.to_datetime(rec['due_date']).strftime('%d/%m/%Y')
                receivables_list.append(rec)
            else:
                # Cria nova conta a receber automaticamente
                status = 'pendente'
                if end_date < today:
                    status = 'vencido'
                
                # Processa múltiplos veículos
                vehicle_ids_str = str(sub.get('vehicle_ids', sub.get('vehicle_id', '')))
                vehicle_ids = [int(vid.strip()) for vid in vehicle_ids_str.split(',') if vid.strip()]
                vehicle_count = len(vehicle_ids)
                
                # Obtém modelos dos veículos
                vehicles_df = pd.read_csv(os.path.join(DATA_DIR, 'vehicles.csv'))
                vehicle_models = []
                for vid in vehicle_ids:
                    vehicle = vehicles_df[vehicles_df['id'] == vid]
                    if len(vehicle) > 0:
                        v = vehicle.iloc[0]
                        vehicle_models.append(f"{v['model']}")
                
                vehicles_text = ', '.join(vehicle_models) if vehicle_models else 'Veículo não encontrado'
                
                new_receivable = {
                    'id': next_id,
                    'subscription_id': sub['id'],
                    'customer_id': sub['customer_id'],
                    'description': f'Assinatura {plan_name} - {customer_name} - {vehicles_text}',
                    'amount': float(sub['amount']),
                    'due_date': sub['end_date'],
                    'payment_date': '',
                    'status': status,
                    'payment_method': '',
                    'notes': f'Gerado automaticamente da assinatura #{sub["id"]}',
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'updated_at': ''
                }
                
                receivables_df = pd.concat([receivables_df, pd.DataFrame([new_receivable])], ignore_index=True)
                
                new_receivable['customer_name'] = customer_name
                new_receivable['due_date_formatted'] = pd.to_datetime(new_receivable['due_date']).strftime('%d/%m/%Y')
                receivables_list.append(new_receivable)
                next_id += 1
        
        # Salva contas a receber atualizadas
        receivables_df.to_csv(os.path.join(DATA_DIR, 'accounts_receivable.csv'), index=False)
        
        # Filtros
        search = request.args.get('search', '').strip()
        status_filter = request.args.get('status', '')
        customer_filter = request.args.get('customer', '')
        
        # Aplicar filtros
        if search:
            search_lower = search.lower()
            receivables_list = [r for r in receivables_list if 
                              search_lower in r.get('customer_name', '').lower() or
                              search_lower in r.get('description', '').lower()]
        
        if status_filter:
            receivables_list = [r for r in receivables_list if r['status'] == status_filter]
        
        if customer_filter:
            receivables_list = [r for r in receivables_list if r.get('customer_id') == int(customer_filter)]
        
        # Calcula totais (antes da paginação)
        total_pendente = sum(float(r['amount']) for r in receivables_list if r['status'] in ['pendente', 'vencido'])
        total_pago = sum(float(r['amount']) for r in receivables_list if r['status'] == 'pago')
        total_vencido = sum(float(r['amount']) for r in receivables_list if r['status'] == 'vencido')
        
        # Paginação
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(receivables_list)
        total_pages = (total + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        receivables_paginated = receivables_list[start_idx:end_idx]
        
        # Prepara lista de clientes para filtro
        customers_list = [{'id': int(row['id']), 'name': row['name']} for _, row in customers_df.iterrows()]
        
        return render_template('admin/financial/accounts_receivable.html',
                             receivables=receivables_paginated,
                             customers=customers_list,
                             total_pendente=total_pendente,
                             total_pago=total_pago,
                             total_vencido=total_vencido,
                             status_filter=status_filter,
                             page=page,
                             total_pages=total_pages,
                             total=total)
    except Exception as e:
        flash(f'Erro ao carregar contas a receber: {str(e)}', 'danger')
        return render_template('admin/financial/accounts_receivable.html',
                             receivables=[],
                             total_pendente=0,
                             total_pago=0,
                             total_vencido=0,
                             status_filter='all')

@app.route('/admin/financial/accounts-receivable/<int:receivable_id>/receive', methods=['POST'])
@admin_required
def receive_payment(receivable_id):
    try:
        receivables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_receivable.csv'))
        
        # Encontra a conta
        idx = receivables_df[receivables_df['id'] == receivable_id].index
        if len(idx) == 0:
            flash('Conta a receber não encontrada.', 'danger')
            return redirect(url_for('accounts_receivable'))
        
        # Atualiza status para pago
        receivables_df.loc[idx, 'status'] = 'pago'
        receivables_df.loc[idx, 'payment_date'] = datetime.now().strftime('%Y-%m-%d')
        receivables_df.loc[idx, 'payment_method'] = request.form.get('payment_method', 'dinheiro')
        receivables_df.loc[idx, 'updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        receivables_df.to_csv(os.path.join(DATA_DIR, 'accounts_receivable.csv'), index=False)
        
        # Registra transação financeira
        transactions_df = pd.read_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'))
        receivable = receivables_df.loc[idx].iloc[0]
        
        new_transaction_id = transactions_df['id'].max() + 1 if len(transactions_df) > 0 else 1
        new_transaction = {
            'id': new_transaction_id,
            'description': receivable['description'],
            'amount': float(receivable['amount']),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'category': 'assinatura',
            'type': 'receita',
            'related_id': receivable['subscription_id'],
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        transactions_df = pd.concat([transactions_df, pd.DataFrame([new_transaction])], ignore_index=True)
        transactions_df.to_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'), index=False)
        
        flash('Pagamento recebido com sucesso!', 'success')
        return redirect(url_for('accounts_receivable'))
        
    except Exception as e:
        flash(f'Erro ao registrar pagamento: {str(e)}', 'danger')
        return redirect(url_for('accounts_receivable'))

# --- Contas a Pagar ---
@app.route('/admin/financial/accounts-payable')
@admin_required
def accounts_payable():
    try:
        payables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'))
        payables_df['due_date'] = pd.to_datetime(payables_df['due_date'])
        
        # Filtros
        search = request.args.get('search', '').strip()
        status_filter = request.args.get('status', '')
        category_filter = request.args.get('category', '')
        
        # Formata dados para exibição
        payables_list = []
        for _, row in payables_df.iterrows():
            payable = row.to_dict()
            payable['due_date_raw'] = row['due_date']
            payable['due_date'] = row['due_date'].strftime('%d/%m/%Y')
            payable['is_overdue'] = row['status'] == 'pendente' and row['due_date'] < pd.Timestamp.now()
            payables_list.append(payable)
        
        # Aplicar filtros
        if search:
            search_lower = search.lower()
            payables_list = [p for p in payables_list if 
                           search_lower in p.get('supplier', '').lower() or
                           search_lower in p.get('description', '').lower()]
        
        if status_filter:
            payables_list = [p for p in payables_list if p.get('status') == status_filter]
        
        if category_filter:
            payables_list = [p for p in payables_list if p.get('category') == category_filter]
        
        # Calcula totais (antes da paginação)
        total_geral = sum(p.get('amount', 0) for p in payables_list)
        total_pago = sum(p.get('amount', 0) for p in payables_list if p.get('status') == 'pago')
        total_vencido = sum(p.get('amount', 0) for p in payables_list if p.get('is_overdue', False))
        
        # Paginação
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(payables_list)
        total_pages = (total + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        payables_paginated = payables_list[start_idx:end_idx]
        
        return render_template('admin/financial/accounts_payable.html',
                             payables=payables_paginated,
                             total_geral=total_geral,
                             total_pago=total_pago,
                             total_vencido=total_vencido,
                             status_filter=status_filter,
                             page=page,
                             total_pages=total_pages,
                             total=total)
    except FileNotFoundError:
        return render_template('admin/financial/accounts_payable.html',
                             payables=[],
                             total_geral=0,
                             total_pago=0,
                             total_vencido=0,
                             status_filter='',
                             page=1,
                             total_pages=0,
                             total=0)

@app.route('/admin/financial/accounts-payable/add', methods=['POST'])
@admin_required
def add_account_payable():
    try:
        payables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'))
        
        # Obtém próximo ID
        next_id = payables_df['id'].max() + 1 if len(payables_df) > 0 and not pd.isna(payables_df['id'].max()) else 1
        
        # Cria nova conta a pagar
        new_payable = {
            'id': next_id,
            'supplier': request.form.get('supplier'),
            'description': request.form.get('description'),
            'category': request.form.get('category'),
            'amount': float(request.form.get('amount')),
            'due_date': request.form.get('due_date'),
            'payment_date': '',
            'status': 'pendente',
            'payment_method': '',
            'notes': request.form.get('notes', ''),
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': ''
        }
        
        payables_df = pd.concat([payables_df, pd.DataFrame([new_payable])], ignore_index=True)
        payables_df.to_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'), index=False)
        
        flash('Conta a pagar adicionada com sucesso!', 'success')
        return redirect(url_for('accounts_payable'))
        
    except Exception as e:
        flash(f'Erro ao adicionar conta: {str(e)}', 'danger')
        return redirect(url_for('accounts_payable'))

@app.route('/admin/financial/accounts-payable/<int:payable_id>/pay', methods=['POST'])
@admin_required
def pay_account(payable_id):
    try:
        payables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'))
        
        # Encontra a conta
        idx = payables_df[payables_df['id'] == payable_id].index
        if len(idx) == 0:
            flash('Conta a pagar não encontrada.', 'danger')
            return redirect(url_for('accounts_payable'))
        
        # Atualiza status para pago
        payables_df.loc[idx, 'status'] = 'pago'
        payables_df.loc[idx, 'payment_date'] = datetime.now().strftime('%Y-%m-%d')
        payables_df.loc[idx, 'payment_method'] = request.form.get('payment_method', 'dinheiro')
        payables_df.loc[idx, 'updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        payables_df.to_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'), index=False)
        
        # Registra transação financeira
        transactions_df = pd.read_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'))
        payable = payables_df.loc[idx].iloc[0]
        
        new_transaction_id = transactions_df['id'].max() + 1 if len(transactions_df) > 0 else 1
        new_transaction = {
            'id': new_transaction_id,
            'description': payable['description'],
            'amount': float(payable['amount']),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'category': payable['category'],
            'type': 'despesa',
            'related_id': payable['id'],
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        transactions_df = pd.concat([transactions_df, pd.DataFrame([new_transaction])], ignore_index=True)
        transactions_df.to_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'), index=False)
        
        flash('Pagamento realizado com sucesso!', 'success')
        return redirect(url_for('accounts_payable'))
        
    except Exception as e:
        flash(f'Erro ao realizar pagamento: {str(e)}', 'danger')
        return redirect(url_for('accounts_payable'))

@app.route('/admin/financial/accounts-payable/<int:payable_id>/edit', methods=['POST'])
@admin_required
def edit_account_payable(payable_id):
    try:
        payables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'))
        
        # Encontra a conta
        idx = payables_df[payables_df['id'] == payable_id].index
        if len(idx) == 0:
            flash('Conta a pagar não encontrada.', 'danger')
            return redirect(url_for('accounts_payable'))
        
        # Atualiza dados
        payables_df.loc[idx, 'supplier'] = request.form.get('supplier')
        payables_df.loc[idx, 'description'] = request.form.get('description')
        payables_df.loc[idx, 'category'] = request.form.get('category')
        payables_df.loc[idx, 'amount'] = float(request.form.get('amount'))
        payables_df.loc[idx, 'due_date'] = request.form.get('due_date')
        payables_df.loc[idx, 'notes'] = request.form.get('notes', '')
        payables_df.loc[idx, 'updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        payables_df.to_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'), index=False)
        
        flash('Conta a pagar atualizada com sucesso!', 'success')
        return redirect(url_for('accounts_payable'))
        
    except Exception as e:
        flash(f'Erro ao atualizar conta: {str(e)}', 'danger')
        return redirect(url_for('accounts_payable'))

@app.route('/admin/financial/accounts-payable/<int:payable_id>/delete', methods=['POST'])
@admin_required
def delete_account_payable(payable_id):
    try:
        payables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'))
        
        # Verifica se a conta existe
        idx = payables_df[payables_df['id'] == payable_id].index
        if len(idx) == 0:
            flash('Conta a pagar não encontrada.', 'danger')
            return redirect(url_for('accounts_payable'))
        
        # Verifica se a conta já foi paga
        if payables_df.loc[idx, 'status'].iloc[0] == 'pago':
            flash('Não é possível excluir uma conta já paga.', 'danger')
            return redirect(url_for('accounts_payable'))
        
        # Remove a conta
        payables_df = payables_df[payables_df['id'] != payable_id]
        payables_df.to_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'), index=False)
        
        flash('Conta a pagar removida com sucesso!', 'success')
        return redirect(url_for('accounts_payable'))
        
    except Exception as e:
        flash(f'Erro ao remover conta: {str(e)}', 'danger')
        return redirect(url_for('accounts_payable'))

# --- Fluxo de Caixa ---
@app.route('/admin/financial/cash-flow')
@admin_required
def cash_flow():
    try:
        # Carrega transações financeiras (receitas e despesas já realizadas)
        transactions_df = pd.read_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'))
        transactions_df['date'] = pd.to_datetime(transactions_df['date'])
        
        # Carrega contas a receber
        receivables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_receivable.csv'))
        receivables_df['due_date'] = pd.to_datetime(receivables_df['due_date'])
        
        # Carrega contas a pagar
        payables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'))
        payables_df['due_date'] = pd.to_datetime(payables_df['due_date'])
        
        # Cria lista de movimentações
        movements = []
        
        # Adiciona transações realizadas (receitas e despesas)
        for _, trans in transactions_df[transactions_df['type'] == 'receita'].iterrows():
            if pd.notna(trans['amount']) and trans['amount'] != '':
                movements.append({
                    'date': trans['date'],
                    'description': trans['description'],
                    'type': 'entrada',
                    'category': trans['category'],
                    'amount': float(trans['amount']),
                    'status': 'realizado'
                })
        
        for _, trans in transactions_df[transactions_df['type'] == 'despesa'].iterrows():
            if pd.notna(trans['amount']) and trans['amount'] != '':
                movements.append({
                    'date': trans['date'],
                    'description': trans['description'],
                    'type': 'saida',
                    'category': trans['category'],
                    'amount': float(trans['amount']),
                    'status': 'realizado'
                })
        
        # Adiciona contas a receber (APENAS pendentes e vencidas - pagas já viraram transações)
        for _, rec in receivables_df.iterrows():
            if pd.notna(rec['amount']) and rec['amount'] != '' and rec['status'] != 'pago':
                # Define status e data baseado no pagamento
                if rec['status'] == 'vencido':
                    status = 'vencido'
                    date = rec['due_date']
                    desc = f"{rec['description']} (Vencido)"
                else:  # pendente
                    status = 'previsto'
                    date = rec['due_date']
                    desc = f"{rec['description']} (A receber)"
                
                movements.append({
                    'date': date,
                    'description': desc,
                    'type': 'entrada',
                    'category': 'assinatura',
                    'amount': float(rec['amount']),
                    'status': status
                })
        
        # Adiciona contas a pagar (APENAS pendentes e vencidas - pagas já viraram transações)
        for _, pay in payables_df.iterrows():
            if pd.notna(pay['amount']) and pay['amount'] != '' and pay['status'] != 'pago':
                # Define status e data baseado no pagamento
                if pay['status'] == 'vencido':
                    status = 'vencido'
                    date = pay['due_date']
                    desc = f"{pay['description']} (Vencido)"
                else:  # pendente
                    status = 'previsto'
                    date = pay['due_date']
                    desc = f"{pay['description']} (A pagar)"
                
                movements.append({
                    'date': date,
                    'description': desc,
                    'type': 'saida',
                    'category': pay.get('category', 'Outros'),
                    'amount': float(pay['amount']),
                    'status': status
                })
        
        # Ordena por data
        movements.sort(key=lambda x: x['date'], reverse=True)
        
        # Aplicar filtros
        search = request.args.get('search', '').strip()
        type_filter = request.args.get('type', '')
        status_filter = request.args.get('status', '')
        
        movements_filtered = movements.copy()
        
        if search:
            search_lower = search.lower()
            movements_filtered = [m for m in movements_filtered if 
                                search_lower in m.get('description', '').lower() or
                                search_lower in m.get('category', '').lower()]
        
        if type_filter:
            movements_filtered = [m for m in movements_filtered if m.get('type') == type_filter]
        
        if status_filter:
            movements_filtered = [m for m in movements_filtered if m.get('status') == status_filter]
        
        # Calcula saldo e totais
        saldo_atual = 0
        entradas_mes = 0
        saidas_mes = 0
        entradas_previstas = 0
        saidas_previstas = 0
        entradas_vencidas = 0
        saidas_vencidas = 0
        
        today = pd.Timestamp.now()
        current_month_start = today.replace(day=1)
        
        for mov in movements:
            if mov['status'] == 'realizado':
                if mov['type'] == 'entrada':
                    saldo_atual += mov['amount']
                    if mov['date'] >= current_month_start:
                        entradas_mes += mov['amount']
                else:
                    saldo_atual -= mov['amount']
                    if mov['date'] >= current_month_start:
                        saidas_mes += mov['amount']
            elif mov['status'] == 'previsto':
                if mov['type'] == 'entrada':
                    entradas_previstas += mov['amount']
                else:
                    saidas_previstas += mov['amount']
            elif mov['status'] == 'vencido':
                if mov['type'] == 'entrada':
                    entradas_vencidas += mov['amount']
                else:
                    saidas_vencidas += mov['amount']
        
        # Paginação
        page = request.args.get('page', 1, type=int)
        per_page = 15
        total = len(movements_filtered)
        total_pages = (total + per_page - 1) // per_page
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        # Formata movimentações para exibição
        movements_display = []
        for mov in movements_filtered[start_idx:end_idx]:
            mov_display = mov.copy()
            mov_display['date'] = mov['date'].strftime('%d/%m/%Y')
            movements_display.append(mov_display)
        
        # Dados para gráfico (últimos 30 dias - apenas realizados)
        last_30_days = [today - pd.Timedelta(days=i) for i in range(30)]
        last_30_days.reverse()
        
        chart_labels = []
        chart_balances = []
        chart_entradas = []
        chart_saidas = []
        
        running_balance = 0
        for day in last_30_days:
            day_movements = [m for m in movements if m['date'].date() == day.date() and m['status'] == 'realizado']
            
            day_entrada = sum(m['amount'] for m in day_movements if m['type'] == 'entrada')
            day_saida = sum(m['amount'] for m in day_movements if m['type'] == 'saida')
            
            running_balance += (day_entrada - day_saida)
            
            chart_labels.append(day.strftime('%d/%m'))
            chart_balances.append(running_balance)
            chart_entradas.append(day_entrada)
            chart_saidas.append(day_saida)
        
        chart_data = {
            'labels': chart_labels,
            'balances': chart_balances,
            'entradas': chart_entradas,
            'saidas': chart_saidas
        }
        
        return render_template('admin/financial/cash_flow.html',
                             movements=movements_display,
                             saldo_atual=saldo_atual,
                             entradas_mes=entradas_mes,
                             saidas_mes=saidas_mes,
                             entradas_previstas=entradas_previstas,
                             saidas_previstas=saidas_previstas,
                             entradas_vencidas=entradas_vencidas,
                             saidas_vencidas=saidas_vencidas,
                             chart_data=chart_data,
                             page=page,
                             total_pages=total_pages,
                             total=total)
    except Exception as e:
        flash(f'Erro ao carregar fluxo de caixa: {str(e)}', 'danger')
        return render_template('admin/financial/cash_flow.html',
                             movements=[],
                             saldo_atual=0,
                             entradas_mes=0,
                             saidas_mes=0,
                             entradas_previstas=0,
                             saidas_previstas=0,
                             entradas_vencidas=0,
                             saidas_vencidas=0,
                             chart_data={'labels': [], 'balances': [], 'entradas': [], 'saidas': []},
                             page=1,
                             total_pages=0,
                             total=0)

# --- Relatórios ---
@app.route('/admin/reports/dre')
@admin_required
def dre_report():
    try:
        # Obtém o ano atual
        current_year = datetime.now().year
        year = int(request.args.get('year', current_year))
        
        # Carrega transações financeiras do ano
        transactions_df = pd.read_csv(os.path.join(DATA_DIR, 'financial_transactions.csv'))
        transactions_df['date'] = pd.to_datetime(transactions_df['date'])
        year_transactions = transactions_df[transactions_df['date'].dt.year == year]
        
        # Carrega contas a pagar PENDENTES para incluir nas despesas
        payables_df = pd.read_csv(os.path.join(DATA_DIR, 'accounts_payable.csv'))
        payables_df['due_date'] = pd.to_datetime(payables_df['due_date'])
        year_payables = payables_df[(payables_df['due_date'].dt.year == year) & 
                                     (payables_df['status'] == 'pendente')]
        
        # Agrupa receitas por categoria
        receitas_grouped = year_transactions[year_transactions['type'] == 'receita'].groupby('category')['amount'].sum()
        receitas_detalhadas = [{'name': cat, 'amount': float(amt)} for cat, amt in receitas_grouped.items()]
        
        # Agrupa despesas por categoria (transações + contas a pagar PENDENTES)
        despesas_grouped = year_transactions[year_transactions['type'] == 'despesa'].groupby('category')['amount'].sum()
        despesas_detalhadas = [{'name': cat, 'amount': float(amt)} for cat, amt in despesas_grouped.items()]
        
        # Adiciona contas a pagar PENDENTES agrupadas por categoria
        payables_grouped = year_payables.groupby('category')['amount'].sum()
        for cat, amt in payables_grouped.items():
            # Procura se a categoria já existe nas despesas
            found = False
            for desp in despesas_detalhadas:
                if desp['name'] == cat:
                    desp['amount'] += float(amt)
                    found = True
                    break
            if not found:
                despesas_detalhadas.append({'name': cat, 'amount': float(amt)})
        
        # Calcula totais
        receita_bruta = float(receitas_grouped.sum()) if not receitas_grouped.empty else 0
        despesas_totais = sum(item['amount'] for item in despesas_detalhadas)
        resultado_liquido = receita_bruta - despesas_totais
        
        # Dados mensais para gráfico de evolução
        monthly_receitas = []
        monthly_despesas = []
        monthly_resultado = []
        for month in range(1, 13):
            month_data = year_transactions[year_transactions['date'].dt.month == month]
            month_payables = year_payables[year_payables['due_date'].dt.month == month]
            
            rec = float(month_data[month_data['type'] == 'receita']['amount'].sum())
            desp = float(month_data[month_data['type'] == 'despesa']['amount'].sum())
            desp += float(month_payables['amount'].sum())  # Apenas pendentes
            
            monthly_receitas.append(rec)
            monthly_despesas.append(desp)
            monthly_resultado.append(rec - desp)
        
        # Dados para gráficos de pizza
        receitas_labels = [item['name'] for item in receitas_detalhadas]
        receitas_data = [item['amount'] for item in receitas_detalhadas]
        despesas_labels = [item['name'] for item in despesas_detalhadas]
        despesas_data = [item['amount'] for item in despesas_detalhadas]
        
        # Prepara os dados para o template
        years = range(current_year - 5, current_year + 1)
        
        return render_template('admin/reports/dre.html',
                             receitas_detalhadas=receitas_detalhadas,
                             despesas_detalhadas=despesas_detalhadas,
                             receita_bruta=receita_bruta,
                             despesas_totais=despesas_totais,
                             resultado_liquido=resultado_liquido,
                             monthly_receitas=monthly_receitas,
                             monthly_despesas=monthly_despesas,
                             monthly_resultado=monthly_resultado,
                             receitas_labels=receitas_labels,
                             receitas_data=receitas_data,
                             despesas_labels=despesas_labels,
                             despesas_data=despesas_data,
                             year=year,
                             years=years)
        
    except Exception as e:
        flash(f'Erro ao gerar relatório DRE: {str(e)}', 'danger')
        return redirect(url_for('admin_dashboard'))

# --- API ---
@app.route('/api/vehicles/by_customer/<int:customer_id>')
@login_required
def get_vehicles_by_customer(customer_id):
    try:
        vehicles_df = read_csv_cached('vehicles.csv')
        if vehicles_df.empty:
            return jsonify([])
        
        # Garante que customer_id está como inteiro no DataFrame
        vehicles_df['customer_id'] = pd.to_numeric(vehicles_df['customer_id'], errors='coerce').fillna(0).astype(int)
        
        # Busca eficiente com loc
        customer_vehicles = vehicles_df.loc[vehicles_df['customer_id'] == customer_id]
        
        vehicles = [{
            'id': int(row['id']),
            'plate': str(row['plate']).upper(),
            'model': str(row['model']),
            'color': str(row['color']) if pd.notna(row['color']) else ''
        } for _, row in customer_vehicles.iterrows()]
        
        return jsonify(vehicles)
    except Exception as e:
        print(f"ERRO ao buscar veículos: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Criação dos diretórios e arquivos CSV se não existirem
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    
    # Estrutura de arquivos CSV
    files_to_create = {
        'users.csv': ['id', 'username', 'password_hash', 'role', 'name', 'created_at', 'updated_at'],
        'customers.csv': ['id', 'name', 'email', 'phone', 'cpf', 'address', 'created_at', 'updated_at'],
        'vehicles.csv': ['id', 'customer_id', 'plate', 'model', 'color', 'created_at'],
        'plans.csv': ['id', 'name', 'description', 'price', 'duration_days', 'is_active', 'created_at', 'updated_at'],
        'subscriptions.csv': ['id', 'customer_id', 'vehicle_id', 'plan_id', 'start_date', 'end_date', 'status', 'created_at'],
        'payments.csv': ['id', 'subscription_id', 'amount', 'payment_date', 'payment_method', 'status', 'created_at'],
        'financial_transactions.csv': ['id', 'description', 'amount', 'date', 'category', 'type', 'related_id', 'created_at']
    }
    
    # Cria os arquivos CSV iniciais se não existirem
    for filename, columns in files_to_create.items():
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            if filename == 'users.csv':
                # Cria usuário admin padrão
                from werkzeug.security import generate_password_hash
                admin_password_hash = generate_password_hash('admin123', method='pbkdf2:sha256')
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                users_df = pd.DataFrame([{
                    'id': 1, 
                    'username': 'admin', 
                    'password_hash': admin_password_hash, 
                    'role': 'admin',
                    'name': 'Administrador',
                    'created_at': current_time,
                    'updated_at': current_time
                }])
                users_df.to_csv(filepath, index=False)
            else:
                # Cria arquivo vazio com as colunas definidas
                df = pd.DataFrame(columns=columns)
                df.to_csv(filepath, index=False)
    
    # Inicia o servidor Flask
    # O reloader_type='stat' usa polling ao invés de watchdog, evitando reloads desnecessários
    app.run(debug=True, use_reloader=True, reloader_type='stat')
