# === This file is part of Calamares - <https://calamares.io> ===
#
#   SPDX-FileCopyrightText: 2026 Kedra Linux
#   SPDX-License-Identifier: BSD-2-Clause
#
find_package(${qtname} COMPONENTS Core Gui Widgets REQUIRED)

calamares_add_plugin(kedraexperience
    TYPE viewmodule
    EXPORT_MACRO PLUGINDLLEXPORT_PRO
    SOURCES
        KedraExperiencePage.cpp
        KedraExperienceViewStep.cpp
    SHARED_LIB
)
