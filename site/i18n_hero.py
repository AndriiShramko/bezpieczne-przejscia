# -*- coding: utf-8 -*-
# Country-localized hero/why strings. Each language leads with the reader's OWN
# country's pedestrian-safety figures (not translated Polish numbers), so the
# stakes feel local. The live demo camera itself is a real Polish crossing —
# the copy is honest about that ("a real crossing, the same tech could watch
# yours"). PL keeps Poland (KRBRD); EN uses global/EU framing (WHO + ETSC).
#   ES -> Spain, DGT 2023
#   RU -> Russia, State Traffic Inspectorate (Госавтоинспекция) 2023
HERO = {
    'es': {
        'kicker': 'Análisis de vídeo en vivo · cámara real · verificación humana',
        'h1': 'En España, cada año más de <span class="hot">350 peatones</span> mueren en accidentes de tráfico.',
        'lead': 'En 2023 murieron 353 peatones en España — 1 de cada 5 víctimas mortales en '
                'carretera (DGT). El 63% ocurre en ciudad, justo donde están los pasos de peatones. '
                'Mostramos EN VIVO un paso real: el sistema cuenta peatones, ciclistas y vehículos, '
                'la IA evalúa cada situación de conflicto y tú verificas sus veredictos. La misma '
                'tecnología abierta podría vigilar cualquier paso — también el de tu ciudad.',
        'facts': [
            ('353', 'peatones muertos en España en 2023 — el 20% de todas las víctimas en carretera (DGT)'),
            ('223', 'de ellos en zona urbana (63%), donde están los pasos de peatones'),
            ('50%', 'tenían 65 años o más — la edad más vulnerable'),
        ],
        'hooks': [
            ('353 / año', 'peatones muertos en España — casi uno al día. Un solo paso bien vigilado '
                          'puede evitar muertes.', 'DGT, 2023'),
            ('20%', 'de todas las muertes en carretera en España son peatones — sin mejora frente a '
                    '2022. El progreso se ha estancado.', 'DGT, 2023'),
            ('−50%', 'objetivo de la UE: la mitad de muertes para 2030, cero para 2050 (Visión Cero / '
                     'Safe System), suscrito por España.', 'Marco de Seguridad Vial de la UE'),
            ('Datos abiertos', 'aportamos las cifras objetivas que faltan en las decisiones de '
                               'infraestructura: cámara real, IA abierta y verificación pública.',
             'Bezpieczne Przejścia / SafeCross'),
        ],
        'why_title': 'Por qué esto importa a los gobiernos',
        'contact_h': 'Hablemos de tu paso de peatones',
        'contact_p': 'Para un municipio, autoridad de carreteras, empresa o proyecto de '
                     'investigación. Respondo en 1–2 días laborables.',
        'cta1': 'Ver la cámara en vivo ↓',
        'cta2': 'Para gobiernos',
    },
    'ru': {
        'kicker': 'Живой видеоанализ · реальная камера · проверка людьми',
        'h1': 'В России каждый год под колёсами гибнет более <span class="hot">3 400 пешеходов</span>.',
        'lead': 'В 2023 году в России в ДТП погибли 3 403 пешехода — почти каждый четвёртый (23,5%) '
                'среди всех погибших на дорогах (Госавтоинспекция). Наезд на пешехода — это каждое '
                'четвёртое ДТП в стране. Мы показываем реальный переход в прямом эфире: система '
                'считает пешеходов, велосипедистов и транспорт, ИИ оценивает каждую конфликтную '
                'ситуацию, а вы проверяете его вердикты. Та же открытая технология может следить за '
                'любым переходом — в том числе в вашем городе.',
        'facts': [
            ('3 403', 'пешехода погибли в ДТП в России в 2023 году — 23,5% всех погибших (Госавтоинспекция)'),
            ('34 944', 'наезда на пешеходов за год — каждое четвёртое ДТП в стране'),
            ('14 504', 'человека всего погибли на дорогах России в 2023 году'),
        ],
        'hooks': [
            ('3 403 / год', 'пешехода гибнут на дорогах России — почти по девять человек каждый день. '
                            'Один переход под наблюдением может спасать жизни.', 'Госавтоинспекция, 2023'),
            ('23,5%', 'всех погибших в ДТП — пешеходы. Это одна из самых незащищённых групп на дороге.',
             'Госавтоинспекция, 2023'),
            ('−50%', 'цель ООН: вдвое сократить смертность на дорогах к 2030 году '
                     '(Десятилетие действий 2021–2030).', 'ВОЗ / ООН'),
            ('Открытые данные', 'мы даём объективные цифры, которых не хватает в решениях по '
                                'инфраструктуре: реальная камера, открытый ИИ и проверка людьми.',
             'Bezpieczne Przejścia / SafeCross'),
        ],
        'why_title': 'Почему это важно для власти',
        'contact_h': 'Обсудим ваш переход',
        'contact_p': 'Для города, дорожной службы, компании или исследовательского проекта. '
                     'Отвечаю за 1–2 рабочих дня.',
        'cta1': 'Смотреть камеру в эфире ↓',
        'cta2': 'Для власти',
    },
}
