/* === This file is part of Calamares - <https://calamares.io> ===
 *
 *   SPDX-FileCopyrightText: 2026 Kedra Linux
 *   SPDX-License-Identifier: GPL-3.0-or-later
 *
 *   Calamares is Free Software: see the License-Identifier above.
 */

#ifndef KEDRAEXPERIENCEVIEWSTEP_H
#define KEDRAEXPERIENCEVIEWSTEP_H

#include "DllMacro.h"
#include "utils/PluginFactory.h"
#include "viewpages/ViewStep.h"

#include <QString>
#include <QVariantMap>

class KedraExperiencePage;

/** @brief Which Kedra desktop experience to install.
 *
 * The installer's entire knowledge of the Kedra Experience is: run
 * `kedra-experience profiles`, show what it describes, and write the chosen
 * selection into GlobalStorage. It holds no module list, no package list and no
 * opinion about which choices are legal -- the resolver owns all three, and
 * `kedraexperiencepackages` asks it again at exec time.
 */
class PLUGINDLLEXPORT KedraExperienceViewStep : public Calamares::ViewStep
{
    Q_OBJECT

public:
    explicit KedraExperienceViewStep( QObject* parent = nullptr );
    ~KedraExperienceViewStep() override;

    QString prettyName() const override;

    QWidget* widget() override;

    bool isNextEnabled() const override;
    bool isBackEnabled() const override;

    bool isAtBeginning() const override;
    bool isAtEnd() const override;

    void onLeave() override;

    Calamares::JobList jobs() const override;

    void setConfigurationMap( const QVariantMap& configurationMap ) override;

private:
    void loadProfiles();

    KedraExperiencePage* m_widget = nullptr;

    QString m_resolver;
    QString m_basePreset;
    int m_profilesSchemaVersion = 1;
    bool m_loaded = false;
};

CALAMARES_PLUGIN_FACTORY_DECLARATION( KedraExperienceViewStepFactory )

#endif  // KEDRAEXPERIENCEVIEWSTEP_H
