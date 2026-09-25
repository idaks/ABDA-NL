targetScope = 'resourceGroup'

param location string = resourceGroup().location
param jobName string = 'abda-nl-migrate'
param containerAppsEnvironmentName string

@description('Public GHCR image repository without a tag or digest, for example ghcr.io/idaks/abda-nl.')
param imageRepository string

@description('The 64-character hexadecimal sha256 digest of the public ABDA-NL GHCR image.')
@minLength(64)
@maxLength(64)
param imageSha256 string

var image = '${imageRepository}@sha256:${imageSha256}'

param postgresHost string
param postgresAdminLogin string = 'abdaadmin'

@secure()
param postgresAdminPassword string

param postgresAppLogin string = 'abda_app'

@secure()
@minLength(32)
param postgresAppPassword string

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: containerAppsEnvironmentName
}

var adminDatabaseUrl = 'postgresql+psycopg://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgresHost}:5432/abda?sslmode=require'

resource migrationJob 'Microsoft.App/jobs@2025-01-01' = {
  name: jobName
  location: location
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: [
        {
          name: 'admin-database-url'
          #disable-next-line use-secure-value-for-secure-inputs
          value: adminDatabaseUrl
        }
        {
          name: 'app-database-password'
          value: postgresAppPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: image
          command: [
            '/opt/venv/bin/python'
          ]
          args: [
            '-m'
            'app.cli.migrate'
          ]
          env: [
            {
              name: 'ABDA_DATABASE_URL'
              secretRef: 'admin-database-url'
            }
            {
              name: 'ABDA_DATABASE_APP_LOGIN'
              value: postgresAppLogin
            }
            {
              name: 'ABDA_DATABASE_APP_PASSWORD'
              secretRef: 'app-database-password'
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
    }
  }
}

output migrationJobName string = migrationJob.name
