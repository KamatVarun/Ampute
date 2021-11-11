import random


def getvar(i):
    end = ''
    end += chr(i % 26 + 65)
    if i > 25:
        i = i // 26 - 1
        end = getvar(i) + end
    return end


class column:
    def __init__(self, datatype='ordinal', classes=5, proportions=None, distribution='normalvariate'):
        self.datatype = datatype
        self.classes = classes
        self.proportions = proportions
        self.distribution = distribution

    def generate_numerical_column(self, cases, a, b):
        if self.distribution == 'uniform':
            return [random.uniform(a, b) for i in range(cases)]
        elif self.distribution == 'betavariate':
            return [random.betavariate(a, b) for i in range(cases)]
        elif self.distribution == 'gammavariate':
            return [random.gammavariate(a, b) for i in range(cases)]
        elif self.distribution == 'gauss':
            return [random.gauss(a, b) for i in range(cases)]
        elif self.distribution == 'normalvariate':
            return [random.normalvariate(a, b) for i in range(cases)]
        elif self.distribution == 'weibullvariate':
            return [random.weibullvariate(a, b) for i in range(cases)]
        else:
            return print('Invalid Distribution')

    def generate_nominal_column(self, cases):
        if self.datatype == 'ordinal':
            names = ['rank' + str(i) for i in range(self.classes)]
        else:
            names = ['var_' + getvar(i) for i in range(self.classes)]
        if not self.proportions:
            column = random.choices(names, k=cases)
        else:
            if len(names) - len(self.proportions) != 0:
                print("Error, proportions need to be defined for every class")
                return False
            column = random.choices(names, k=cases, weights=self.proportions)
        return column
