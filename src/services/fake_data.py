from faker import Faker

fake = Faker("pt_BR")

def gerar_nome() -> str:
    return fake.name()

def gerar_email() -> str:
    return fake.email()

def gerar_telefone() -> str:
    return fake.phone_number()

def gerar_cpf() -> str:
    return fake.cpf()

def gerar_empresa() -> str:
    return fake.company()

def gerar_endereco() -> str:
    return fake.address().replace("\n", ", ")
