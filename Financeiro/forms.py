from django import forms

from .models import Budget, Categoria, ContaBancaria, ContaPagarReceber, Evento, RecorrenciaConta


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css_class = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            if isinstance(field.widget, forms.CheckboxInput):
                css_class = 'form-check-input'
            field.widget.attrs.setdefault('class', css_class)


class ContaBancariaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ContaBancaria
        fields = ['nome', 'banco', 'agencia', 'numero', 'ativa']


class CategoriaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ['nome', 'tipo', 'pai', 'ativa']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pais = Categoria.objects.filter(pai__isnull=True, ativa=True)
        if self.instance and self.instance.pk:
            pais = pais.exclude(pk=self.instance.pk)
        self.fields['pai'].queryset = pais
        self.fields['pai'].required = False
        self.fields['pai'].empty_label = 'Sem conta pai'
        self.fields['pai'].help_text = 'Deixe vazio para criar uma conta pai. Preencha para criar uma conta filho.'

    def clean(self):
        cleaned_data = super().clean()
        pai = cleaned_data.get('pai')
        tipo = cleaned_data.get('tipo')

        if self.instance and self.instance.pk:
            if pai and self.instance.filhas.exists():
                self.add_error('pai', 'Uma conta pai com filhos nao pode virar conta filho.')
            if not pai and tipo and self.instance.filhas.exclude(tipo=tipo).exists():
                self.add_error('tipo', 'A conta pai deve manter o mesmo tipo das contas filho.')

        return cleaned_data


class EventoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Evento
        fields = ['nome', 'ativa']


class ImportacaoOFXForm(BootstrapFormMixin, forms.Form):
    conta = forms.ModelChoiceField(queryset=ContaBancaria.objects.filter(ativa=True), label='Conta bancaria')
    arquivo = forms.FileField(label='Arquivo OFX')

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        if not arquivo.name.lower().endswith('.ofx'):
            raise forms.ValidationError('Envie um arquivo com extensao .ofx.')
        return arquivo


class ImportacaoCartaoForm(BootstrapFormMixin, forms.Form):
    conta = forms.ModelChoiceField(queryset=ContaBancaria.objects.filter(ativa=True), label='Conta/cartao')
    arquivo = forms.FileField(label='Arquivo CSV')

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        if not arquivo.name.lower().endswith('.csv'):
            raise forms.ValidationError('Envie um arquivo com extensao .csv.')
        return arquivo


class BudgetForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Budget
        fields = ['categoria', 'mes', 'valor']
        widgets = {
            'mes': forms.DateInput(attrs={'type': 'date'}),
        }


class ContaPagarReceberForm(BootstrapFormMixin, forms.ModelForm):
    criar_recorrencia = forms.BooleanField(
        label='Criar recorrencia',
        required=False,
    )
    frequencia_recorrencia = forms.ChoiceField(
        choices=RecorrenciaConta.FREQUENCIA_CHOICES,
        label='Frequencia',
        required=False,
    )
    quantidade_recorrencia = forms.IntegerField(
        label='Quantidade de contas',
        min_value=2,
        max_value=120,
        required=False,
        help_text='Inclui a primeira conta informada no vencimento.',
    )

    class Meta:
        model = ContaPagarReceber
        fields = ['categoria', 'evento', 'descricao', 'vencimento', 'valor', 'status']
        widgets = {
            'vencimento': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria'].queryset = Categoria.objects.filter(ativa=True, pai__isnull=False)
        self.fields['categoria'].empty_label = 'Selecione uma categoria'
        self.fields['categoria'].error_messages['required'] = 'Selecione uma categoria antes de salvar.'
        self.fields['evento'].queryset = Evento.objects.filter(ativa=True)
        self.fields['evento'].required = False
        self.fields['evento'].empty_label = 'Sem evento'
        self.fields['frequencia_recorrencia'].initial = RecorrenciaConta.MENSAL

    def clean(self):
        cleaned_data = super().clean()
        criar_recorrencia = cleaned_data.get('criar_recorrencia')
        frequencia = cleaned_data.get('frequencia_recorrencia')
        quantidade = cleaned_data.get('quantidade_recorrencia')

        if criar_recorrencia:
            if not frequencia:
                self.add_error('frequencia_recorrencia', 'Selecione a frequencia da recorrencia.')
            if not quantidade:
                self.add_error('quantidade_recorrencia', 'Informe quantas contas devem ser geradas.')

        return cleaned_data
